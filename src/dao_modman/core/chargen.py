"""Merge the `chargenmorphcfg.xml` files of several mods into one.

Every character creation mod ships a full copy of this file with its own entries added, and
the game only reads one of them. Merging is what the "CharGenMorph Compiler" tool does.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

FILE_NAME = "chargenmorphcfg.xml"
ROOT_TAG = "morph_config"


def _key(element: ET.Element) -> tuple[str, str]:
    return element.tag, (element.get("name") or "").casefold()


def _merge_into(dest: ET.Element, source: ET.Element) -> None:
    existing = {_key(child): child for child in dest}
    for child in source:
        match = existing.get(_key(child))
        if match is None:
            added = copy.deepcopy(child)
            dest.append(added)
            existing[_key(added)] = added
        elif len(child):  # a section: merge its entries
            _merge_into(match, child)


def merge(sources: list[Path]) -> tuple[bytes | None, list[Path]]:
    """Union of all entries, in the order first seen. Returns `(xml, unreadable files)`."""
    merged: ET.Element | None = None
    failed: list[Path] = []
    for source in sources:
        try:
            root = ET.parse(source).getroot()
        except (ET.ParseError, OSError):
            failed.append(source)
            continue
        if root.tag != ROOT_TAG:
            failed.append(source)
        elif merged is None:
            merged = copy.deepcopy(root)
        else:
            _merge_into(merged, root)
    if merged is None:
        return None, failed
    ET.indent(merged)
    return ET.tostring(merged, encoding="utf-8", xml_declaration=True), failed


GAME = "(game)"  # entries the unmodded game already has


@dataclass
class Section:
    name: str
    total: int = 0
    added: dict[str, int] = field(default_factory=dict)  # who introduced how many entries


@dataclass
class Dangling:
    """An entry that names a resource no archive and no enabled mod provides."""

    section: str
    name: str
    mods: list[str]  # the mods whose file lists it


@dataclass
class Report:
    mods: list[str] = field(default_factory=list)  # contributing mods, in load order
    unreadable: list[str] = field(default_factory=list)  # mods whose file cannot be parsed
    sections: list[Section] = field(default_factory=list)
    dangling: list[Dangling] = field(default_factory=list)


def _entries(root: ET.Element) -> list[tuple[str, tuple[str, ...], str]]:
    """Every `<resource>` as `(section, path of tags, name)`."""
    found = []

    def walk(element: ET.Element, path: tuple[str, ...]) -> None:
        for child in element:
            if child.tag == "resource":
                found.append((path[0] if path else "", path, child.get("name") or ""))
            else:
                walk(child, (*path, child.tag))

    walk(root, ())
    return found


def _parse(data: bytes | Path) -> ET.Element | None:
    try:
        root = ET.parse(data).getroot() if isinstance(data, Path) else ET.fromstring(data)
    except (ET.ParseError, OSError):
        return None
    return root if root.tag == ROOT_TAG else None


def analyse(
    sources: list[tuple[str, Path]],
    game_file: bytes | None,
    exists: Callable[[str], bool],
) -> Report:
    """Describe what merging `sources` (mod id, file) yields, and what in it is broken.

    Every mod ships the game's full list plus its own additions, so an entry is credited to
    the game if the unmodded file has it, and otherwise to the first mod that lists it.
    """
    report = Report()
    game_root = _parse(game_file) if game_file is not None else None
    introduced: dict[tuple[tuple[str, ...], str], str] = {}
    if game_root is not None:
        for _section, path, name in _entries(game_root):
            introduced.setdefault((path, name.casefold()), GAME)
    listed: dict[tuple[tuple[str, ...], str], list[str]] = {}
    sections: dict[str, Section] = {}
    labels: dict[tuple[tuple[str, ...], str], tuple[str, str]] = {}
    for mod_id, path in sources:
        root = _parse(path)
        if root is None:
            report.unreadable.append(mod_id)
            continue
        report.mods.append(mod_id)
        for section, tags, name in _entries(root):
            key = (tags, name.casefold())
            listed.setdefault(key, [])
            if mod_id not in listed[key]:
                listed[key].append(mod_id)
            labels.setdefault(key, (section, name))
            introduced.setdefault(key, mod_id)
    for key, (section, name) in labels.items():
        stats = sections.setdefault(section, Section(section))
        stats.total += 1
        stats.added[introduced[key]] = stats.added.get(introduced[key], 0) + 1
        if name and not exists(name):
            report.dangling.append(Dangling(section, name, listed[key]))
    report.sections = list(sections.values())
    report.dangling.sort(key=lambda entry: (entry.section, entry.name.casefold()))
    return report


def format_report(report: Report, names: dict[str, str]) -> str:
    """The report as text; `names` maps mod ids to what to call them."""

    def label(mod_id: str) -> str:
        return names.get(mod_id, mod_id)

    lines: list[str] = []
    if len(report.mods) > 1:
        lines.append(f"Entries of {len(report.mods)} mods are merged into one {FILE_NAME}:")
    else:
        lines.append(f"One mod ships a {FILE_NAME}; it is installed as it is:")
    lines += [f"  {label(mod_id)}" for mod_id in report.mods]
    if report.unreadable:
        lines.append("")
        lines.append("Could not be read and is left out:")
        lines += [f"  {label(mod_id)}" for mod_id in report.unreadable]
    lines.append("")
    width = max((len(section.name) for section in report.sections), default=0)
    lines.append(f"{'Section':<{width}}  entries  from")
    for section in report.sections:
        parts = [f"game {section.added[GAME]}"] if GAME in section.added else []
        parts += [f"{label(m)} +{n}" for m, n in section.added.items() if m != GAME]
        lines.append(f"{section.name:<{width}}  {section.total:>7}  {', '.join(parts)}")
    lines.append("")
    if not report.dangling:
        lines.append("Every entry has a file behind it.")
        return "\n".join(lines)
    count = len(report.dangling)
    lines.append(
        f"{count} entr{'y points' if count == 1 else 'ies point'} at nothing. These show up as "
        "broken choices in the character creator:"
    )
    for entry in report.dangling:
        lines.append(
            f"  {entry.section}: {entry.name}   (listed by {', '.join(map(label, entry.mods))})"
        )
    return "\n".join(lines)
