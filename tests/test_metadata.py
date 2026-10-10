from __future__ import annotations

import tomllib
from datetime import UTC, datetime
from pathlib import Path

import pytest

from dao_modman.core import metadata
from dao_modman.core.models import ArchiveInfo, Mod, NexusInfo, PathOp, Profile, ProfileEntry


def test_mod_round_trip(tmp_path: Path) -> None:
    mod = Mod(
        id="my-mod",
        name="My Mod",
        version="1.0",
        kind="dazip",
        archive=ArchiveInfo("My Mod-1-1-0.zip", "aa", "bb"),
        nexus=NexusInfo("dragonage", 1, 22, "My Mod-1-1-0.zip", datetime(2020, 1, 2, tzinfo=UTC)),
        addin_uids=["uid1"],
        ops=[PathOp("remove", "docs"), PathOp("rename", "a/b", "c/d")],
    )
    metadata.save_mod(tmp_path, mod)
    assert metadata.load_mod(tmp_path) == mod

    raw = tomllib.loads((tmp_path / "mod.toml").read_text())
    assert raw["ops"] == [
        {"op": "remove", "path": "docs"},
        {"op": "rename", "from": "a/b", "to": "c/d"},
    ]
    assert raw["nexus"]["mod_id"] == 1


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
    profile = Profile([ProfileEntry("a"), ProfileEntry("b", enabled=False, group="Textures")])
    metadata.save_profile(path, profile)
    assert metadata.load_profile(path) == profile


def test_profile_move() -> None:
    profile = Profile([ProfileEntry("a"), ProfileEntry("b"), ProfileEntry("c")])
    assert profile.move("a", 1)
    assert [entry.id for entry in profile.mods] == ["b", "a", "c"]
    assert not profile.move("b", -1)
    assert not profile.move("c", 1)
