"""Predefined path operations for mods that only work when installed at a specific place.

The list ships with dao-modman (`assets/presets.toml`); a `presets.toml` in the user's
config directory adds to it and wins where both describe the same mod file.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, replace
from importlib import resources
from pathlib import Path

from . import metadata, pathops
from .library import Library
from .models import Mod, PathOp

SHIPPED = "assets/presets.toml"
PLACEHOLDER = "{mod}"  # the mod's own override folder


@dataclass(frozen=True)
class Preset:
    mod_id: int  # on Nexus
    file_id: int
    description: str
    ops: tuple[PathOp, ...]  # with PLACEHOLDER still in the paths


@dataclass
class Presets:
    by_file: dict[tuple[int, int], Preset] = field(default_factory=dict)
    problems: list[str] = field(default_factory=list)

    def find(self, mod: Mod) -> Preset | None:
        """The preset for the Nexus file this mod was installed from, if there is one."""
        if mod.nexus is None:
            return None
        return self.by_file.get((mod.nexus.mod_id, mod.nexus.file_id))


def expand(preset: Preset, mod: Mod) -> list[PathOp]:
    """The preset's operations with the placeholder filled in for this mod."""
    folder = f"{pathops.OVERRIDE_DIR}/{mod.id}"

    def fill(path: str | None) -> str | None:
        return path.replace(PLACEHOLDER, folder) if path is not None else None

    return [pathops.validate(replace(op, path=fill(op.path), to=fill(op.to))) for op in preset.ops]


def pending(mod: Mod, preset: Preset | None) -> list[PathOp]:
    """The preset's operations the mod does not have yet; empty if it is fully applied."""
    if preset is None:
        return []
    return [op for op in expand(preset, mod) if op not in mod.ops]


def apply(library: Library, mod: Mod, preset: Preset) -> int:
    """Add the preset's missing operations to the mod. Returns how many were added."""
    todo = pending(mod, preset)
    if todo:
        mod.ops = [*mod.ops, *todo]
        library.save_mod(mod, f"{mod.id}: apply predefined path operations ({len(todo)})")
    return len(todo)


def _parse(text: str) -> list[Preset]:
    found = []
    for entry in tomllib.loads(text).get("preset", []):
        ops = tuple(metadata.op_from_dict(op) for op in entry.get("ops", []))
        if not ops:
            raise ValueError(f"preset for mod {entry.get('mod_id')} has no operations")
        found.append(
            Preset(
                mod_id=int(entry["mod_id"]),
                file_id=int(entry["file_id"]),
                description=str(entry.get("description", "")).strip(),
                ops=ops,
            )
        )
    return found


def load(user_file: Path | None = None) -> Presets:
    """The shipped presets, with the user's file on top. A bad user file is reported, not fatal."""
    presets = Presets()
    shipped = resources.files("dao_modman").joinpath(SHIPPED).read_text(encoding="utf-8")
    for preset in _parse(shipped):
        presets.by_file[(preset.mod_id, preset.file_id)] = preset
    if user_file is None or not user_file.is_file():
        return presets
    try:
        own = _parse(user_file.read_text(encoding="utf-8"))
    except (OSError, ValueError, KeyError, TypeError, metadata.MetadataError) as exc:
        presets.problems.append(f"{user_file}: {exc}")
        return presets
    for preset in own:  # the user's entry replaces a shipped one for the same file
        presets.by_file[(preset.mod_id, preset.file_id)] = preset
    return presets


def describe(mod: Mod, preset: Preset) -> str:
    """What the preset is for and what it would do to this mod."""
    lines = [f"Predefined path operations exist for {mod.name}:", ""]
    if preset.description:
        lines += [preset.description, ""]
    lines += [f"  {op.describe()}" for op in pending(mod, preset)]
    return "\n".join(lines)
