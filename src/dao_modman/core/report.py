"""Conflict report: which mods ship the same file, where, and whose copy the game gets."""

from __future__ import annotations

from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path

from . import chargen, resources
from .deploy import ACCEPTED, ERROR, OPEN, UNMANAGED, Conflict, Plan
from .library import Library
from .models import Overwrite

USED = "\u2190 used"
ACCEPTED_MARK = "\u2713 accepted"
ERROR_MARK = "\u2717 wrong order"
UNUSED_TITLE = "Unused overwrite rules (the other mod is not in the library)"
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


_RANK = {ERROR: 0, OPEN: 1, ACCEPTED: 2}


def conflict_groups(library: Library, plan: Plan, mod_id: str | None = None) -> list[Group]:
    """Conflicts grouped by the mods involved: errors first, then open, then accepted ones.

    With `mod_id`, only the groups that mod is part of.
    """
    labels = _labels(library)
    groups: dict[tuple[str, ...], Group] = {}
    for conflict in sorted(plan.conflicts, key=lambda c: (_RANK[c.status], c.name.casefold())):
        key = tuple(conflict.mod_ids)
        if mod_id is not None and mod_id not in key:
            continue
        if key not in groups:
            groups[key] = Group([labels[mod_id] for mod_id in key], [])
        groups[key].conflicts.append(conflict)

    def order(group: Group) -> tuple:
        worst = min(_RANK[conflict.status] for conflict in group.conflicts)
        return worst, [position for position, _name in reversed(group.mods)]

    return sorted(groups.values(), key=order)


def group_line(group: Group) -> str:
    count = len(group.conflicts)
    note = f"{count} file{'' if count == 1 else 's'}"
    if all(conflict.status == ACCEPTED for conflict in group.conflicts):
        note += ", accepted"
    return f"{group.title}   ({note})"


def file_line(library: Library, conflict: Conflict) -> str:
    """The file name, with a mark if the conflict is accepted or in the wrong order."""
    if conflict.status == ACCEPTED:
        return f"{conflict.name}   {ACCEPTED_MARK}"
    if conflict.status == ERROR:
        labels = _labels(library)

        def label(mod_id: str) -> str:
            return _label(*labels[mod_id])

        problems = "; ".join(
            f"{label(early)} is set to overwrite {label(late)} but loads before it"
            for early, late in conflict.misordered
        )
        return f"{conflict.name}   {ERROR_MARK}: {problems}"
    return conflict.name


def select_conflicts(plan: Plan, names: list[str]) -> tuple[list[Conflict], list[str]]:
    """Open conflicts picked by file name (globs allowed) or mod id; all of them if no names.

    Returns `(conflicts, names that matched nothing)`.
    """
    candidates = [c for c in plan.with_status(OPEN) if UNMANAGED not in c.mod_ids]
    if not names:
        return candidates, []
    selected: list[Conflict] = []
    unmatched: list[str] = []
    for name in names:
        wanted = name.casefold()
        hits = [
            conflict
            for conflict in candidates
            if name in conflict.mod_ids
            or wanted in {conflict.name.casefold(), conflict.name.rpartition("/")[2].casefold()}
            or fnmatchcase(conflict.name.casefold(), wanted)
        ]
        if not hits:
            unmatched.append(name)
        selected += [conflict for conflict in hits if conflict not in selected]
    return selected, unmatched


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


def unused_rules(library: Library, mod_id: str | None = None) -> list[tuple[str, Overwrite]]:
    """Overwrite rules naming a mod that is no longer in the library: `(declaring mod, rule)`.

    They do nothing, but would silently apply again if that mod came back. With `mod_id`,
    only the rules that mod declares.
    """
    return [
        (mod.id, overwrite)
        for _entry, mod in library.ordered()
        for overwrite in mod.overwrites
        if overwrite.mod not in library.mods and mod_id in (None, mod.id)
    ]


def has_conflicts(library: Library, plan: Plan, mod_id: str | None = None) -> bool:
    """Whether the conflict report has anything to show, for the library or for one mod."""
    return bool(
        conflict_groups(library, plan, mod_id)
        or merged_lines(library, plan, mod_id)
        or unused_rules(library, mod_id)
    )


def unused_line(library: Library, mod_id: str, overwrite: Overwrite) -> str:
    position, name = _labels(library)[mod_id]
    files = ", ".join(overwrite.files) or "every file"
    return f"#{position} {name} may overwrite {overwrite.mod}: {files}"


def format_conflicts(library: Library, plan: Plan) -> str:
    """The report as plain text; empty if there is nothing to report."""
    lines: list[str] = []
    for group in conflict_groups(library, plan):
        lines.append(group_line(group))
        for conflict in group.conflicts:
            lines.append(f"  {file_line(library, conflict)}")
            lines += [f"    {line}" for line in provider_lines(library, conflict)]
        lines.append("")
    if merged := merged_lines(library, plan):
        lines.append("Merged (no mod loses):")
        lines += [f"  {line}" for line in merged]
        lines.append("")
    if unused := unused_rules(library):
        lines.append(UNUSED_TITLE + ":")
        lines += [f"  {unused_line(library, mod_id, rule)}" for mod_id, rule in unused]
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
