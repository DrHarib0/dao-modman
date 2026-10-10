"""Merge the `chargenmorphcfg.xml` files of several mods into one.

Every character creation mod ships a full copy of this file with its own entries added, and
the game only reads one of them. Merging is what the "CharGenMorph Compiler" tool does.
"""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
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
