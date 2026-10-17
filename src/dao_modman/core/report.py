"""Conflict report: which mods ship the same file, where, and whose copy the game gets."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import chargen, resources
from .deploy import UNMANAGED, Conflict, Plan
from .library import Library

USED = "\u2190 used"
UNMANAGED_NAME = "not managed by dao-modman"


@dataclass
class Group:
    """All conflicts between one set of mods."""

    mods: list[tuple[int, str]]  # (load order position, name), in load order: the last wins
    conflicts: list[Conflict]

    @property
    def title(self) -> str:
        return "  <  ".join(_label(position, name) for position, name in self.mods)


def _labels(library: Library) -> dict[str, tuple[int, str]]:
    """Load order position and name of every mod; files we do not manage come after all."""
    labels = {
        entry.id: (position, mod.name) for position, (entry, mod) in enumerate(library.ordered(), 1)
    }
    labels[UNMANAGED] = (len(labels) + 1, UNMANAGED_NAME)
    return labels


def _label(position: int, name: str) -> str:
    return name if name == UNMANAGED_NAME else f"#{position} {name}"


def conflict_groups(library: Library, plan: Plan, mod_id: str | None = None) -> list[Group]:
    """Conflicts grouped by the mods involved, in the load order of their winners.

    With `mod_id`, only the groups that mod is part of.
    """
    labels = _labels(library)
    groups: dict[tuple[str, ...], Group] = {}
    for conflict in sorted(plan.conflicts, key=lambda c: c.name.casefold()):
        key = tuple(conflict.mod_ids)
        if mod_id is not None and mod_id not in key:
            continue
        if key not in groups:
            groups[key] = Group([labels[mod_id] for mod_id in key], [])
        groups[key].conflicts.append(conflict)

    def order(group: Group) -> list[int]:
        return [position for position, _name in reversed(group.mods)]

    return sorted(groups.values(), key=order)


def group_line(group: Group) -> str:
    count = len(group.conflicts)
    return f"{group.title}   ({count} file{'' if count == 1 else 's'})"


def provider_lines(library: Library, conflict: Conflict) -> list[str]:
    """One line per copy of the file: mod and folder, the winner's copies marked."""
    labels = _labels(library)
    names = [_label(*labels[p.mod_id]) for p in conflict.providers]
    width = max(map(len, names))
    lines = []
    for name, provider in zip(names, conflict.providers, strict=True):
        for index, target in enumerate(provider.targets):
            folder = target.rpartition("/")[0] + "/"
            mark = f"   {USED}" if provider is conflict.winner else ""
            lines.append(f"{name if index == 0 else '':<{width}}   {folder}{mark}")
    return lines


def merged_lines(library: Library, plan: Plan, mod_id: str | None = None) -> list[str]:
    """Files merged from several mods; with `mod_id`, only those it contributes to."""
    labels = _labels(library)
    return [
        f"{target}  from " + ", ".join(f"#{labels[m][0]} {labels[m][1]}" for m in mod_ids)
        for target, mod_ids in plan.merged.items()
        if mod_id is None or mod_id in mod_ids
    ]


def has_conflicts(library: Library, plan: Plan, mod_id: str | None = None) -> bool:
    """Whether the conflict report has anything to show, for the library or for one mod."""
    return bool(conflict_groups(library, plan, mod_id) or merged_lines(library, plan, mod_id))


def format_conflicts(library: Library, plan: Plan) -> str:
    """The report as plain text; empty if there is nothing to report."""
    lines: list[str] = []
    for group in conflict_groups(library, plan):
        lines.append(group_line(group))
        for conflict in group.conflicts:
            lines.append(f"  {conflict.name}")
            lines += [f"    {line}" for line in provider_lines(library, conflict)]
        lines.append("")
    if merged := merged_lines(library, plan):
        lines.append("Merged (no mod loses):")
        lines += [f"  {line}" for line in merged]
        lines.append("")
    return "\n".join(lines).rstrip("\n")


def chargen_report(
    install_dir: Path | None, library: Library, progress: resources.Progress | None = None
) -> tuple[chargen.Report | None, str]:
    """What the character creator list of the enabled mods contains, and what in it is broken.

    Returns `(report, text)`; the report is None when no enabled mod ships the file.
    """
    sources = [
        (mod.id, library.files_dir(mod.id) / source)
        for entry, mod in library.ordered()
        if entry.enabled
        for target, source in library.targets(mod).items()
        if target.rpartition("/")[2].casefold() == chargen.FILE_NAME
    ]
    if not sources:
        return None, f"No enabled mod ships a {chargen.FILE_NAME}."
    available = resources.collect(install_dir, library, progress)
    game_file = resources.read_game_file(install_dir, chargen.FILE_NAME)
    result = chargen.analyse(sources, game_file, available.exists)
    names = {mod_id: f"#{position} {name}" for mod_id, (position, name) in _labels(library).items()}
    text = chargen.format_report(result, names)
    if install_dir is None:
        text += "\n\nThe game install dir is unknown, so the game's own files were not considered."
    return result, text
