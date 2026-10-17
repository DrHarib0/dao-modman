from __future__ import annotations

import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from dao_modman.core import adopt, deploy
from dao_modman.core.library import Library
from dao_modman.core.models import KIND_DAZIP, KIND_OVERRIDE, KIND_PACKAGE

OVERRIDE = "packages/core/override"
ADDINS_XML = """<?xml version="1.0" encoding="utf-8"?>
<AddInsList>
  <AddInItem UID="hand_addin" Name="hand_addin" ExtendedModuleUID="Single Player"
             Priority="50" Enabled="1" State="2" Format="1" BioWare="0">
    <Title DefaultText="The Hand Made AddIn" />
    <Version>2.1</Version>
  </AddInItem>
  <AddInItem UID="DAO_PRC_CP_1" Name="dlc" Enabled="1" BioWare="1">
    <Title DefaultText="Official DLC" />
  </AddInItem>
</AddInsList>
"""


def write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)
    return path


@pytest.fixture
def modded(tmp_path: Path, user_dir: Path) -> Path:
    """A game user dir with mods installed by hand, official DLC and some junk."""
    write(user_dir / f"{OVERRIDE}/Better Hair/hair.mmh", "hair")
    write(user_dir / f"{OVERRIDE}/Better Hair/sub/hair.dds", "texture")
    write(user_dir / f"{OVERRIDE}/loose.erf", "loose")
    write(user_dir / f"{OVERRIDE}/amulet/amulet.uti", "named like its mod id")
    write(user_dir / "AddIns/hand_addin/module/data/hand.gda", "gda")
    write(user_dir / "AddIns/leftover/core/old.erf", "unregistered AddIn folder")
    write(user_dir / "packages/core/data/fonts.erf", "font")
    write(user_dir / "AddIns/DAO_PRC_CP_1/module/dlc.erf", "official")
    write(user_dir / f"{OVERRIDE}/Better Hair/Thumbs.db", "junk")  # goes along with its folder
    write(user_dir / f"{OVERRIDE}/Junk/desktop.ini", "only ignored files here")
    target = write(tmp_path / "elsewhere.erf", "outside")
    (user_dir / f"{OVERRIDE}/link.erf").symlink_to(target)
    write(user_dir / "Settings/AddIns.xml", ADDINS_XML)
    return user_dir


def registered(user_dir: Path) -> list[str]:
    root = ET.parse(user_dir / "Settings/AddIns.xml").getroot()
    return [item.get("UID") for item in root.iter("AddInItem")]


def test_find_groups(library: Library, modded: Path) -> None:
    found = adopt.find(library, modded, library.dirs.deployed_file)
    groups = {group.name: sorted(rel for rel, _ in group.files) for group in found.groups}
    assert groups == {
        "Better Hair": [f"{OVERRIDE}/Better Hair/hair.mmh", f"{OVERRIDE}/Better Hair/sub/hair.dds"],
        adopt.LOOSE_NAME: [f"{OVERRIDE}/loose.erf"],
        "amulet": [f"{OVERRIDE}/amulet/amulet.uti"],
        "hand_addin": ["AddIns/hand_addin/module/data/hand.gda"],
        "leftover": ["AddIns/leftover/core/old.erf"],
        adopt.OTHER_NAME: ["packages/core/data/fonts.erf"],
    }  # the official DLC is not among them
    by_name = {group.name: group for group in found.groups}
    assert by_name["hand_addin"].addin is not None  # registered: becomes a dazip
    assert by_name["leftover"].addin is None
    assert found.links == [f"{OVERRIDE}/link.erf"]
    assert by_name["Better Hair"].extras == [
        (f"{OVERRIDE}/Better Hair/Thumbs.db", modded / f"{OVERRIDE}/Better Hair/Thumbs.db")
    ]
    assert found.ignored == [f"{OVERRIDE}/Junk/desktop.ini"]  # no group to go with
    text = adopt.describe(found)
    assert "The Hand Made AddIn (AddIn, 1 file(s))" in text and "Better Hair (files" in text
    assert "1 ignored file(s) (readmes etc.) go along" in text
    assert "1 other ignored file(s) are left alone" in text


def test_adopt_then_apply_restores_everything(library: Library, modded: Path) -> None:
    before = {
        rel: path.read_text()
        for rel, path in deploy.unmanaged(modded, library.dirs.deployed_file)
        if not path.is_symlink() and not rel.endswith((".db", ".ini"))
    }
    found = adopt.find(library, modded, library.dirs.deployed_file)
    result = adopt.adopt(library, modded, found)

    mods = {mod.name: mod for mod in result.mods}
    assert set(mods) == {
        "Better Hair",
        adopt.LOOSE_NAME,
        "amulet",
        "The Hand Made AddIn",  # named after the AddIn's title, with its version
        "leftover",
        adopt.OTHER_NAME,
    }
    assert mods["The Hand Made AddIn"].kind == KIND_DAZIP
    assert mods["The Hand Made AddIn"].version == "2.1"
    # override files are plain loose-file mods, installed to their own folder like any mod
    for name in ("Better Hair", "amulet", adopt.LOOSE_NAME):
        assert mods[name].kind == KIND_OVERRIDE and mods[name].ops == []
    # files outside the override folder can only be placed by an override package
    assert mods["leftover"].kind == mods[adopt.OTHER_NAME].kind == KIND_PACKAGE
    assert all((library.dirs.downloads / mod.archive.file).is_file() for mod in result.mods)
    # appended to the load order, enabled
    assert [entry.id for entry in library.profile.mods] == [mod.id for mod in result.mods]
    assert all(entry.enabled for entry in library.profile.mods)

    # the originals are gone, with the junk of their folder; the official DLC, the link and
    # the lone junk are not
    assert result.removed == len(before) + 1
    assert not any((modded / rel).exists() for rel in before)
    assert not (modded / f"{OVERRIDE}/loose.erf").exists()
    assert (modded / "AddIns/DAO_PRC_CP_1/module/dlc.erf").is_file()
    assert (modded / f"{OVERRIDE}/link.erf").is_symlink()
    assert not (modded / f"{OVERRIDE}/Better Hair").exists()
    assert (library.files_dir(mods["Better Hair"].id) / "Thumbs.db").is_file()  # in the library
    assert (modded / f"{OVERRIDE}/Junk/desktop.ini").is_file()
    assert not (modded / "AddIns/hand_addin").exists()  # emptied folders are removed
    assert (modded / OVERRIDE).is_dir()  # the folders the game expects stay
    assert result.unregistered == ["hand_addin"] and registered(modded) == ["DAO_PRC_CP_1"]

    # apply installs everything again, now managed: override files in their mod's folder
    # (the game finds them by name), everything else exactly where it was
    moved = {
        f"{OVERRIDE}/Better Hair/": f"{OVERRIDE}/better-hair/",
        f"{OVERRIDE}/loose.erf": f"{OVERRIDE}/loose-override-files/loose.erf",
    }

    def now(rel: str) -> str:
        return next(
            (new + rel[len(old) :] for old, new in moved.items() if rel.startswith(old)), rel
        )

    plan = deploy.build_plan(library)
    deploy.apply(plan, modded, library.dirs.deployed_file)
    assert {rel: (modded / now(rel)).read_text() for rel in before} == before
    assert sorted(registered(modded)) == ["DAO_PRC_CP_1", "hand_addin"]
    assert adopt.find(library, modded, library.dirs.deployed_file).groups == []


def test_failure_leaves_the_game_dir_alone(
    library: Library, modded: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    found = adopt.find(library, modded, library.dirs.deployed_file)
    real_verify = adopt._verify
    calls = []

    def verify_fails_late(lib, mod, group):
        calls.append(mod.id)
        if len(calls) == 3:
            raise adopt.AdoptError("copy differs")
        real_verify(lib, mod, group)

    monkeypatch.setattr(adopt, "_verify", verify_fails_late)
    with pytest.raises(adopt.AdoptError, match="nothing adopted: copy differs"):
        adopt.adopt(library, modded, found)
    assert library.mods == {} and library.profile.mods == []  # imported mods removed again
    assert list(library.dirs.downloads.iterdir()) == []  # and their archives
    for group in found.groups:
        assert all(path.is_file() for _, path in group.files)
    assert registered(modded) == ["hand_addin", "DAO_PRC_CP_1"]


def test_nothing_to_adopt(library: Library, user_dir: Path) -> None:
    found = adopt.find(library, user_dir, library.dirs.deployed_file)
    assert found.groups == [] and found.file_count == 0
    assert adopt.adopt(library, user_dir, found).mods == []
