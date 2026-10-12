from __future__ import annotations

import tomllib
from pathlib import Path

from dao_modman.core import pathops, presets
from dao_modman.core.library import Library
from dao_modman.core.models import Mod, NexusInfo, PathOp

from .conftest import make_zip

OVERRIDE = "packages/core/override"


def nexus_mod(library: Library, tmp_path: Path, name: str, mod_id: int, file_id: int, files) -> Mod:
    archive = make_zip(tmp_path / f"{name}.zip", files)
    return library.import_archive(archive, nexus=NexusInfo("dragonage", mod_id, file_id))


def test_shipped_presets_are_valid() -> None:
    """Every shipped entry loads, has a description and operations that validate."""
    shipped = presets.load()
    assert shipped.problems == []
    assert {(1825, 9280), (1110, 5571)} <= set(shipped.by_file)
    probe = Mod(id="some-mod", name="Some Mod")
    for preset in shipped.by_file.values():
        assert preset.description and preset.ops
        for op in presets.expand(preset, probe):
            assert "{" not in op.path and "{" not in (op.to or "")
            pathops.validate(op)


def test_find_expand_and_apply(tmp_path: Path, library: Library) -> None:
    shipped = presets.load()
    files = {
        "ADD_ITEM_DELUXE_1_1/siramods_items/get.ncs": "x",
        "ADD_ITEM_DELUXE_1_1/README.txt": "r",
    }
    mod = nexus_mod(library, tmp_path, "Add Any Item", 1825, 9280, files)
    other_file = nexus_mod(library, tmp_path, "Older file", 1825, 9271, files)
    local = library.import_archive(make_zip(tmp_path / "local.zip", {"a.erf": "a"}))

    assert shipped.find(other_file) is None  # same mod, another file: no preset
    assert shipped.find(local) is None  # not from Nexus
    preset = shipped.find(mod)
    assert preset.description.startswith("The bundled exe has hardcoded paths.")

    # {mod} becomes this mod's own override folder
    wanted = PathOp(
        "rename",
        f"{OVERRIDE}/add-any-item/ADD_ITEM_DELUXE_1_1/siramods_items",
        f"{OVERRIDE}/siramods_items",
    )
    assert presets.pending(mod, preset) == [wanted]
    text = presets.describe(mod, preset)
    assert "Add Any Item" in text and wanted.describe() in text

    assert presets.apply(library, mod, preset) == 1
    assert presets.apply(library, mod, preset) == 0  # applying twice adds nothing
    reloaded = Library(library.dirs).mods[mod.id]
    assert reloaded.ops == [wanted] and presets.pending(reloaded, preset) == []
    assert library.targets(mod) == {
        f"{OVERRIDE}/siramods_items/get.ncs": "ADD_ITEM_DELUXE_1_1/siramods_items/get.ncs"
    }
    assert presets.pending(local, None) == []


def test_existing_operations_are_kept(tmp_path: Path, library: Library) -> None:
    mod = nexus_mod(library, tmp_path, "Fonts", 1110, 5571, {"fonts.erf": "f", "extra.erf": "e"})
    own = PathOp("remove", f"{OVERRIDE}/fonts/extra.erf")
    mod.ops = [own]
    preset = presets.load().find(mod)
    presets.apply(library, mod, preset)
    assert mod.ops == [
        own,
        PathOp("rename", f"{OVERRIDE}/fonts/fonts.erf", "packages/core/data/fonts.erf"),
    ]


USER_FILE = """
[[preset]]
mod_id = 1825
file_id = 9280
description = "My own way"
[[preset.ops]]
op = "remove"
path = "{mod}/ADD_ITEM_DELUXE_1_1/README.txt"

[[preset]]
mod_id = 4242
file_id = 7
description = "A mod only I know"
[[preset.ops]]
op = "rename"
from = "{mod}/data"
to = "packages/core/data"
"""


def test_user_file_adds_and_overrides(tmp_path: Path) -> None:
    user_file = tmp_path / "presets.toml"
    assert presets.load(user_file).by_file == presets.load().by_file  # a missing file is fine

    user_file.write_text(USER_FILE)
    loaded = presets.load(user_file)
    assert loaded.problems == []
    assert loaded.by_file[(1825, 9280)].description == "My own way"  # replaces the shipped entry
    assert [op.op for op in loaded.by_file[(1825, 9280)].ops] == ["remove"]
    assert loaded.by_file[(4242, 7)].description == "A mod only I know"
    assert (1110, 5571) in loaded.by_file  # other shipped entries are untouched


def test_broken_user_file_is_reported_not_fatal(tmp_path: Path) -> None:
    user_file = tmp_path / "presets.toml"
    broken = [
        "this is not toml [",
        '[[preset]]\nmod_id = 1\nfile_id = 2\ndescription = "no operations"\n',
        '[[preset]]\nfile_id = 2\n[[preset.ops]]\nop = "remove"\npath = "x"\n',
        "[[preset]]\nmod_id = 1\nfile_id = 2\n[[preset.ops]]\n"
        'op = "rename"\nfrom = "a"\nto = "../../x"\n',
    ]
    for text in broken:
        user_file.write_text(text)
        loaded = presets.load(user_file)
        assert len(loaded.problems) == 1 and str(user_file) in loaded.problems[0], text
        assert loaded.by_file == presets.load().by_file  # the shipped presets still work
    assert tomllib.loads(USER_FILE)["preset"]  # the documented format itself is valid TOML
