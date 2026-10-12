from __future__ import annotations

from pathlib import Path

import pytest

from dao_modman.core import gff, moditems
from dao_modman.core.library import Library

from .conftest import make_zip
from .gamefiles import make_gda, make_tlk, make_uti
from .test_chargen_report import make_erf


def test_readers(tmp_path: Path) -> None:
    fields = gff.read_gff3(make_uti("blade", strref=12, text="Blade", cost=7))
    assert fields["LocalizedName"] == gff.LocString(12, ("Blade",))
    assert fields["Cost"] == 7 and fields["TemplateResRef"] == "blade"
    assert gff.read_gff3(make_uti("x"))["LocalizedName"] == gff.LocString(None)
    for bad in (b"", b"UTI V3.2" + bytes(8), make_uti("blade")[:70], b"GFF V4.0" + bytes(60)):
        with pytest.raises(gff.GffError):
            gff.read_gff3(bad)

    table = make_tlk(tmp_path / "core_en-us.tlk", {5: "Five", 6: None, 9: "Nine"})
    assert gff.read_tlk(table) == {5: "Five", 9: "Nine"}
    (tmp_path / "bad.tlk").write_bytes(b"GFF V4.0PC  TLK V0.2" + bytes(4))
    for bad in (tmp_path / "bad.tlk", tmp_path / "missing.tlk"):
        with pytest.raises(gff.GffError):
            gff.read_tlk(bad)


@pytest.fixture
def install(tmp_path: Path) -> Path:
    path = tmp_path / "install"
    make_erf(path / "packages/core/data/designeritems.erf", {"Gen_Sword.uti": make_uti("s")})
    kinds = make_gda([(5, "Weapon - Longsword", 0), (6, "Armor - Helmet", 2), (7, "Misc", 0)])
    make_erf(path / "packages/core/data/2da.erf", {"BITM_base.gda": kinds, "other.gda": b"x"})
    make_tlk(path / "packages/core/data/talktables/core_en-us.tlk", {1: "Game One", 2: "Game Two"})
    make_tlk(path / "packages/core/data/talktables/core_de-de.tlk", {1: "Spiel Eins"})
    return path


def test_collect(tmp_path: Path, install: Path, library: Library) -> None:
    packed = make_erf(
        tmp_path / "packed.erf",
        {"in_erf.uti": make_uti("in_erf", strref=3), "model.mmh": b"m"},
    ).read_bytes()
    mod = library.import_archive(
        make_zip(
            tmp_path / "Armory.zip",
            {
                "a/game_name.uti": make_uti("game_name", strref=1),
                "a/own_name.uti": make_uti("own_name", strref=2, text="ignored: the table has it"),
                "a/embedded.uti": make_uti(
                    "embedded", strref=999, text="  Embedded Text ", base_item=77
                ),
                "a/nameless.uti": make_uti("nameless", base_item=6),
                "bitm_base_armory.gda": make_gda([(77, "Trinket", 0)]),
                "a/broken.uti": b"not an item file",
                "a/gen_sword.UTI": make_uti("gen_sword", text="Changed Sword"),
                "packed.erf": packed,
                "other.erf": "not an archive",
                "armory_en-us.tlk": make_tlk(tmp_path / "own.tlk", {2: "Mine"}).read_bytes(),
            },
        )
    )
    other = library.import_archive(
        make_zip(
            tmp_path / "Other.zip",
            {
                "o_en-us.tlk": make_tlk(
                    tmp_path / "o.tlk", {2: "Theirs", 3: "Theirs 3"}
                ).read_bytes(),
                "elsewhere.uti": make_uti("elsewhere", text="Not Mine"),
            },
        )
    )

    seen: list[str] = []
    game = moditems.GameData()
    found = moditems.collect(
        library, mod, install, game, progress=lambda phase, _done, _total: seen.append(phase)
    )
    assert [(item.name, item.file, item.named) for item in found.added] == [
        ("broken.uti", "broken.uti", False),
        ("Embedded Text", "embedded.uti", True),  # no table has the id: the file's own text
        ("Game One", "game_name.uti", True),
        ("Mine", "own_name.uti", True),  # the mod's own table wins over another mod's
        ("nameless.uti", "nameless.uti", False),
        ("Theirs 3", "in_erf.uti", True),  # from an archive, named by an enabled mod's table
    ]
    assert found.replaced == 1  # gen_sword.UTI replaces the game's item
    kinds = {item.file: item.type for item in found.added}
    assert kinds["game_name.uti"] == "Longsword"  # from the label, after its group
    assert kinds["nameless.uti"] == "Mine"  # the type's own name, from the talk tables
    assert kinds["embedded.uti"] == "Trinket"  # a kind the mod itself defines
    assert kinds["broken.uti"] == ""
    assert len(found.problems) == 1 and found.problems[0].startswith("broken.uti:")
    assert {"Reading game items", "Reading names"} <= set(seen)

    # another mod's table only counts while that mod is switched on
    library.profile.entry(other.id).enabled = False
    names = {item.file: item.name for item in moditems.collect(library, mod, install, game).added}
    assert names["in_erf.uti"] == "in_erf.uti" and names["own_name.uti"] == "Mine"

    # a switched-off mod is still listed, with its own names
    library.profile.entry(mod.id).enabled = False
    assert len(moditems.collect(library, mod, install, game).added) == 6

    german = moditems.collect(library, mod, install, game, "de-de")
    assert {item.file: item.name for item in german.added}["game_name.uti"] == "Spiel Eins"

    # without the game everything counts as added and only the mods' tables give names
    alone = moditems.collect(library, mod, None, moditems.GameData())
    assert alone.replaced == 0 and len(alone.added) == 7

    plain = library.import_archive(make_zip(tmp_path / "Plain.zip", {"a.erf": "x"}))
    nothing = moditems.collect(library, plain, install, game)
    assert (nothing.added, nothing.replaced, nothing.problems) == ([], 0, [])


def test_talk_tables_are_read_once(tmp_path: Path, install: Path, library: Library, monkeypatch):
    mod = library.import_archive(make_zip(tmp_path / "M.zip", {"i.uti": make_uti("i", strref=1)}))
    reads: list[Path] = []
    real = gff.read_tlk
    monkeypatch.setattr(gff, "read_tlk", lambda path: reads.append(path) or real(path))
    game = moditems.GameData()
    for _ in range(2):
        assert moditems.collect(library, mod, install, game).added[0].name == "Game One"
    assert len(reads) == 1
    table = install / "packages/core/data/talktables/core_en-us.tlk"
    make_tlk(table, {1: "Renamed in a longer table"})
    assert moditems.collect(library, mod, install, game).added[0].name.startswith("Renamed")
