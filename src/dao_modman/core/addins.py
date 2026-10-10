"""Register / unregister dazip AddIns in the game's `Settings/AddIns.xml`."""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path

from .dazip import AddIn
from .metadata import write_atomic

ADDINS_REL = "Settings/AddIns.xml"


def _load(path: Path) -> ET.Element:
    if path.is_file():
        root = ET.parse(path).getroot()
        if root.tag != "AddInsList":
            raise ValueError(f"{path}: unexpected root element <{root.tag}>")
        return root
    return ET.Element("AddInsList")


def _save(path: Path, root: ET.Element) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ET.indent(root)
    write_atomic(path, ET.tostring(root, encoding="utf-8", xml_declaration=True))


def register(addins_xml: Path, addins: list[AddIn]) -> list[str]:
    """Add missing AddIns; returns the UIDs that were actually added."""
    if not addins:
        return []
    root = _load(addins_xml)
    present = {item.get("UID") for item in root.iter("AddInItem")}
    added = []
    for addin in addins:
        if addin.uid in present:
            continue
        root.append(copy.deepcopy(addin.element))
        present.add(addin.uid)
        added.append(addin.uid)
    if added:
        _save(addins_xml, root)
    return added


def unregister(addins_xml: Path, uids: list[str]) -> list[str]:
    """Remove AddIns by UID; returns the UIDs that were removed."""
    if not uids or not addins_xml.is_file():
        return []
    root = _load(addins_xml)
    removed = []
    for item in list(root.findall("AddInItem")):
        if item.get("UID") in uids:
            root.remove(item)
            removed.append(item.get("UID"))
    if removed:
        _save(addins_xml, root)
    return removed
