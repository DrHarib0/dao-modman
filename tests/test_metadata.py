from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest
import tomli_w

from dao_modman.core import metadata
from dao_modman.core.models import (
    ArchiveInfo,
    Mod,
    NexusInfo,
    Overwrite,
    PathOp,
    Profile,
    ProfileEntry,
)


def test_mod_round_trip(tmp_path: Path) -> None:
    mod = Mod(
        id="my-mod",
        name="My Mod",
        version="1.0",
        kind="dazip",
        archive=ArchiveInfo("My Mod-1-1-0.zip", "aa", "bb"),
        nexus=NexusInfo("dragonage", 1, 22, "My Mod-1-1-0.zip", datetime(2020, 1, 2, tzinfo=UTC)),
        addin_uids=["uid1"],
        ops=[PathOp("exclude", "docs"), PathOp("include", "docs/keep.txt")],
        overwrites=[Overwrite("other-mod", ["a.gda", "abi_*.gda"]), Overwrite("whole-mod")],
        options={"Human Females/Hand Size": "NB big"},
    )
    metadata.save_mod(tmp_path, mod)
    assert metadata.load_mod(tmp_path) == mod
    assert tomllib.loads((tmp_path / "mod.toml").read_text())["overwrites"] == [
        {"mod": "other-mod", "files": ["a.gda", "abi_*.gda"]},
        {"mod": "whole-mod"},
    ]

    raw = tomllib.loads((tmp_path / "mod.toml").read_text())
    assert raw["ops"] == [
        {"op": "exclude", "path": "docs"},
        {"op": "include", "path": "docs/keep.txt"},
    ]
    assert raw["nexus"]["mod_id"] == 1


@pytest.mark.parametrize(
    "op", [{"op": "remove", "path": "a"}, {"op": "rename", "from": "a", "to": "b"}]
)
def test_operations_of_earlier_versions_are_rejected(tmp_path: Path, op: dict) -> None:
    data = {"id": "m", "name": "M", "ops": [op]}
    (tmp_path / "mod.toml").write_text(tomli_w.dumps(data))
    with pytest.raises(metadata.MetadataError, match="unknown operation"):
        metadata.load_mod(tmp_path)


def test_minimal_mod_has_no_optional_tables(tmp_path: Path) -> None:
    metadata.save_mod(tmp_path, Mod(id="m", name="M"))
    raw = tomllib.loads((tmp_path / "mod.toml").read_text())
    assert set(raw) == {"id", "name", "version", "kind", "added"}


@pytest.mark.parametrize(
    "ops",
    [
        '[[ops]]\nop = "rename"\nfrom = "a"\nto = "../../etc/passwd"\n',
        '[[ops]]\nop = "remove"\npath = "/"\n',
        '[[ops]]\nop = "copy"\npath = "a"\n',
        '[[ops]]\nop = "rename"\nfrom = "a"\n',
    ],
)
def test_hand_edited_bad_ops_are_rejected(tmp_path: Path, ops: str) -> None:
    (tmp_path / "mod.toml").write_text(f'id = "m"\nname = "M"\n{ops}')
    with pytest.raises(metadata.MetadataError):
        metadata.load_mod(tmp_path)


def test_profile_round_trip(tmp_path: Path) -> None:
    path = tmp_path / "profile.toml"
    assert metadata.load_profile(path) == Profile()
    profile = Profile(
        [ProfileEntry("a"), ProfileEntry("b", enabled=False, group="Textures")], hide_official=True
    )
    metadata.save_profile(path, profile)
    assert metadata.load_profile(path) == profile


def test_profile_move() -> None:
    profile = Profile([ProfileEntry("a"), ProfileEntry("b"), ProfileEntry("c")])
    assert profile.move("a", 1)
    assert [entry.id for entry in profile.mods] == ["b", "a", "c"]
    assert not profile.move("b", -1)
    assert not profile.move("c", 1)
