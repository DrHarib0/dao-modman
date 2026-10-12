from __future__ import annotations

from pathlib import Path

import pytest

from dao_modman.core import deploy, itemlists
from dao_modman.core.library import Library
from dao_modman.core.paths import Dirs

from .conftest import make_zip
from .test_chargen_report import make_erf

OVERRIDE = "packages/core/override"
# A table with the single item "a.uti", as the mod's scanner writes it.
ONE_ITEM = bytes.fromhex(
    "4746462056342e30504320204732444156302e320300000094000000"
    "67746f70020000004c00000008000000"
    "636f6c6d020000006400000005000000"
    "726f7773020000007c00000008000000"
    "12270000010000c00000000013270000020000c004000000"
    "112700000400000000000000f72a00000000000004000000"
    "152700000500000000000000162700000e00000004000000"
    "0800000016000000"
    "0200000036c9fb6601a3c4785c00"
    "02000000000000002a000000010000003e000000"
    "0800000049004e00560041004c00490044000000"
    "0600000061002e007500740069000000"
)


def test_table_format() -> None:
    assert itemlists.write_table(["a.uti"]) == ONE_ITEM
    names = ["coa100im_aura_letter.uti", "Gen_Im_Arm.UTI", "a.uti", "a.uti"]
    assert itemlists.read_table(itemlists.write_table(names)) == names  # duplicates survive
    assert itemlists.read_table(itemlists.write_table([])) == []
    for bad in (b"", b"not a table at all, but long enough", ONE_ITEM[:150]):
        with pytest.raises(ValueError):
            itemlists.read_table(bad)


def test_game_items_follow_the_scanner(tmp_path: Path) -> None:
    install = tmp_path / "install"
    make_erf(install / "packages/core/data/designeritems.erf", {"b.uti": b"", "tex.dds": b""})
    make_erf(install / "packages/core/patch/patch001.erf", {"b.uti": b"", "c.UTI": b""})
    make_erf(install / "modules/single player/data/designeritems.erf", {"m.uti": b""})
    make_erf(install / "Addins/dao_prc_ep_1/module/data/designeritems.erf", {"z.uti": b""})
    make_erf(install / "Offers/dao_prc_ocp_13/module/data/designeritems.erf", {"offer.uti": b""})
    (install / "packages/core/data/broken.erf").write_bytes(b"nothing like an archive here")
    locked = make_erf(install / "Addins/dao_prc_cp_1/core/data/designeritems.erf", {"dlc.uti": b""})
    # an ERF V2.2 header with the encryption flag: names readable, the scanner skips it
    head = "ERF V2.2".encode("utf-16-le") + (1).to_bytes(4, "little") + bytes(12)
    locked.write_bytes(head + (0x10).to_bytes(4, "little") + bytes(20) + bytes(76))

    seen: list[tuple[int, int]] = []
    names = itemlists.game_items(install, lambda _phase, done, total: seen.append((done, total)))
    # addins, modules, packages; an item in two archives is listed twice
    assert names == ["z.uti", "m.uti", "b.uti", "b.uti", "c.UTI"]
    assert seen[-1] == (6, 6)


def enabled(library: Library, tmp_path: Path, name: str, files: dict[str, str | bytes]):
    return library.import_archive(make_zip(tmp_path / f"{name}.zip", files))


def packed(tmp_path: Path, members: list[str]) -> bytes:
    return make_erf(tmp_path / "packed.erf", dict.fromkeys(members, b"")).read_bytes()


def test_fill_and_apply(tmp_path: Path, dirs: Dirs, library: Library, user_dir: Path) -> None:
    install = tmp_path / "install"
    make_erf(install / "packages/core/data/designeritems.erf", {"game_sword.uti": b""})
    deployed = dirs.deployed_file

    enabled(library, tmp_path, "Armory", {"armor.erf": packed(tmp_path, ["Plate.uti", "x.mmh"])})
    enabled(library, tmp_path, "Loose", {"sub/Ring.UTI": "r", "fake.erf": "no archive"})
    off = library.import_archive(make_zip(tmp_path / "off.zip", {"hidden.uti": "h"}))
    library.profile.entry(off.id).enabled = False
    by_hand = user_dir / "packages/core/override/manual"
    by_hand.mkdir(parents=True)
    (by_hand / "handmade.uti").write_bytes(b"h")
    make_erf(by_hand / "pack.erf", {"plate.uti": b"", "Boots.uti": b""})

    # the mod is not installed: nothing to do
    plan = deploy.build_plan(library)
    assert itemlists.fill(plan, install, user_dir, deployed) is None
    assert all(item.content is None for item in plan.files)

    files = {"items/itlst_biow.gda": "shipped", "items/ITLST_USER.GDA": "shipped", "get.ncs": "s"}
    enabled(library, tmp_path, "Add Item", files)
    plan = deploy.build_plan(library)
    assert itemlists.fill(plan, install, user_dir, deployed) == (
        "Item tables: 1 game items, 4 mod items"
    )
    generated = {item.target: item.content for item in plan.files if item.content is not None}
    assert set(generated) == {  # only the two tables, wherever the mod puts them
        f"{OVERRIDE}/add-item/items/itlst_biow.gda",
        f"{OVERRIDE}/add-item/items/ITLST_USER.GDA",
    }
    mod_table = generated[f"{OVERRIDE}/add-item/items/ITLST_USER.GDA"]
    # sorted, once each whatever the case; the disabled mod's item is not there
    assert itemlists.read_table(mod_table) == ["Boots.uti", "handmade.uti", "Plate.uti", "Ring.UTI"]

    deploy.apply(plan, user_dir, deployed)
    written = user_dir / OVERRIDE / "add-item/items/itlst_biow.gda"
    assert itemlists.read_table(written.read_bytes()) == ["game_sword.uti"]
    assert (user_dir / OVERRIDE / "add-item/get.ncs").read_text() == "s"

    # a second apply gives the same tables: applied files are not counted as "by hand"
    plan = deploy.build_plan(library)
    itemlists.fill(plan, install, user_dir, deployed)
    again = {item.target: item.content for item in plan.files if item.content is not None}
    assert again == generated

    deploy.unapply(deployed)
    assert not written.exists() and (by_hand / "handmade.uti").exists()

    # without the game, its table stays as shipped; the mods' table is still written
    plan = deploy.build_plan(library)
    note = itemlists.fill(plan, None, user_dir, deployed)
    assert "itlst_biow.gda left as shipped" in note and "4 mod items" in note
    assert [item.target for item in plan.files if item.content is not None] == [
        f"{OVERRIDE}/add-item/items/ITLST_USER.GDA"
    ]
