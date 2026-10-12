from __future__ import annotations

import struct
from pathlib import Path

import pytest

from dao_modman import __main__ as cli
from dao_modman.core import chargen, erf, report, resources
from dao_modman.core.config import Config
from dao_modman.core.library import Library

from .conftest import make_zip


def make_erf(path: Path, members: dict[str, bytes]) -> Path:
    """A plain ERF V2.0 archive."""
    path.parent.mkdir(parents=True, exist_ok=True)
    offset = 32 + 72 * len(members)
    toc, body = b"", b""
    for name, data in members.items():
        toc += name.encode("utf-16-le").ljust(64, b"\0") + struct.pack("<2I", offset, len(data))
        offset += len(data)
        body += data
    header = "ERF V2.0".encode("utf-16-le") + struct.pack("<4I", len(members), 2009, 1, 0)
    path.write_bytes(header + toc + body)
    return path


def cfg(hairs: list[str], colors: list[str]) -> bytes:
    hair = "".join(f'<resource name="{name}" cut="1" />' for name in hairs)
    color = "".join(f'<resource name="{name}" />' for name in colors)
    return (
        "<morph_config><hairs><human_male>"
        f"{hair}</human_male></hairs><hair_colors>{color}</hair_colors></morph_config>"
    ).encode()


@pytest.fixture
def install(tmp_path: Path) -> Path:
    """A game with two vanilla hairs, one colour and its own chargenmorphcfg.xml."""
    path = tmp_path / "install"
    make_erf(
        path / "packages/core/data/misc.erf",
        {"chargenmorphcfg.xml": cfg(["hm_har_a_0", "hm_har_b_0", ""], ["t3_har_blk"])},
    )
    make_erf(
        path / "packages/core/data/models.erf",
        {"hm_har_a_0.mmh": b"m", "HM_HAR_B_0.msh": b"m", "t3_har_blk.tnt": b"t"},
    )
    return path


def test_archive_reader(tmp_path: Path) -> None:
    archive = erf.open_archive(make_erf(tmp_path / "a.erf", {"x.mmh": b"abc", "y.tnt": b"de"}))
    assert [entry.name for entry in archive.entries] == ["x.mmh", "y.tnt"]
    assert erf.read(archive, archive.entries[1]) == b"de"
    (tmp_path / "bad.erf").write_bytes(b"not an archive, though long enough to hold a header")
    with pytest.raises(erf.ErfError):
        erf.open_archive(tmp_path / "bad.erf")


def test_report_credits_and_dangling_entries(
    tmp_path: Path, install: Path, library: Library
) -> None:
    library.import_archive(
        make_zip(
            tmp_path / "Hair Pack.zip",
            {
                "chargenmorphcfg.xml": cfg(
                    ["hm_har_a_0", "hm_har_b_0", "", "hm_har_new_0", "hm_har_typo_0_0"],
                    ["t3_har_blk"],
                ),
                "models/hm_har_new_0.mmh": "model",
            },
        )
    )
    library.import_archive(
        make_zip(
            tmp_path / "Colours.zip",
            {
                "chargenmorphcfg.xml": cfg(["hm_har_a_0"], ["t3_har_blk", "T3_HAR_PNK", "t3_gone"]),
                "packed.erf": make_erf(tmp_path / "p.erf", {"t3_har_pnk.tnt": b"t"}).read_bytes(),
            },
        )
    )
    library.import_archive(make_zip(tmp_path / "Broken.zip", {"chargenmorphcfg.xml": "<oops"}))
    off = library.import_archive(
        make_zip(tmp_path / "Off.zip", {"chargenmorphcfg.xml": cfg(["nope"], [])})
    )
    library.profile.entry(off.id).enabled = False

    seen: list[tuple[str, int, int]] = []
    result, text = report.chargen_report(install, library, lambda *step: seen.append(step))
    assert result.mods == ["hair-pack", "colours"] and result.unreadable == ["broken"]
    sections = {s.name: (s.total, s.added) for s in result.sections}
    assert sections == {
        "hairs": (5, {chargen.GAME: 3, "hair-pack": 2}),
        "hair_colors": (3, {chargen.GAME: 1, "colours": 2}),
    }
    # the typo and the colour without a tint file are broken; the "none" entry is not
    assert [(d.section, d.name, d.mods) for d in result.dangling] == [
        ("hair_colors", "t3_gone", ["colours"]),
        ("hairs", "hm_har_typo_0_0", ["hair-pack"]),
    ]
    assert seen[-1][0] == "Reading archives" and seen[-1][1] == seen[-1][2]

    lines = text.splitlines()
    assert lines[0] == "Entries of 2 mods are merged into one chargenmorphcfg.xml:"
    assert lines[1:3] == ["  #1 Hair Pack", "  #2 Colours"]
    assert "  #3 Broken" in lines
    assert "hairs              5  game 3, #1 Hair Pack +2" in lines
    assert "hair_colors        3  game 1, #2 Colours +2" in lines
    assert "  hairs: hm_har_typo_0_0   (listed by #1 Hair Pack)" in lines
    assert any(line.startswith("2 entries point at nothing") for line in lines)


def test_report_without_problems_or_without_files(
    tmp_path: Path, install: Path, library: Library
) -> None:
    result, text = report.chargen_report(install, library)
    assert result is None and text == "No enabled mod ships a chargenmorphcfg.xml."

    library.import_archive(
        make_zip(tmp_path / "ok.zip", {"chargenmorphcfg.xml": cfg(["hm_har_a_0"], ["t3_har_blk"])})
    )
    result, text = report.chargen_report(install, library)
    assert result.dangling == []
    assert text.splitlines()[0] == "One mod ships a chargenmorphcfg.xml; it is installed as it is:"
    assert text.splitlines()[-1] == "Every entry has a file behind it."

    # without a game install nothing of the game can be vouched for, and the text says so
    result, text = report.chargen_report(None, library)
    assert sorted(d.name for d in result.dangling) == ["hm_har_a_0", "t3_har_blk"]
    assert "install dir is unknown" in text


def test_resources(tmp_path: Path, install: Path, library: Library) -> None:
    found = resources.collect(install, library)
    assert found.exists("HM_HAR_A_0") and found.exists("hm_har_b_0.msh")
    assert not found.exists("hm_har_c_0") and not found.exists("")
    assert resources.read_game_file(install, "ChargenMorphCfg.xml").startswith(b"<morph_config>")
    assert resources.read_game_file(install, "missing.xml") is None
    (install / "packages/core/data/broken.erf").write_bytes(b"x" * 64)
    assert len(resources.collect(install, library).problems) == 1


def test_cli(
    tmp_path: Path,
    install: Path,
    library: Library,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config = Config(dirs=library.dirs, game_install_dir=install)
    monkeypatch.setattr(cli, "load_config", lambda: config)
    assert cli.main(["chargen"]) == 0
    assert "No enabled mod" in capsys.readouterr().out
    library.import_archive(
        make_zip(tmp_path / "bad.zip", {"chargenmorphcfg.xml": cfg(["hm_har_missing"], [])})
    )
    assert cli.main(["chargen"]) == 1  # broken entries make it fail
    assert "hairs: hm_har_missing" in capsys.readouterr().out
