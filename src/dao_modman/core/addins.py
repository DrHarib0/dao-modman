"""Register / unregister dazip AddIns in the game's `Settings/AddIns.xml`."""

from __future__ import annotations

import copy
import xml.etree.ElementTree as ET
from pathlib import Path

from .dazip import AddIn, addin_from_element
from .metadata import write_atomic
from .paths import resolve_case

ADDINS_REL = "Settings/AddIns.xml"
SHIPPED_REL = "Addins"  # in the game's install dir

MANAGED = "managed"  # registered by one of our mods
OFFICIAL = "official"  # the game's own DLC
EXTERNAL = "external"  # installed by hand or by another tool


class AddInsError(Exception):
    pass


def list_registered(user_dir: Path) -> list[AddIn]:
    """Everything registered in the game's `Settings/addins.xml`."""
    addins_xml = resolve_case(user_dir, ADDINS_REL)
    if not addins_xml.is_file():
        return []
    try:
        root = _load(addins_xml)
    except (ET.ParseError, OSError, ValueError) as exc:
        raise AddInsError(f"cannot read {addins_xml}: {exc}") from exc
    return [addin for item in root.findall("AddInItem") if (addin := addin_from_element(item))]


def shipped_uids(install_dir: Path | None) -> set[str]:
    """Case-folded UIDs of the AddIns that ship in the game's install dir."""
    if install_dir is None:
        return set()
    shipped = resolve_case(install_dir, SHIPPED_REL)
    if not shipped.is_dir():
        return set()
    return {entry.name.casefold() for entry in shipped.iterdir() if entry.is_dir()}


def classify(addin: AddIn, managed: set[str], shipped: set[str]) -> str:
    """Tell our mods, official DLC and foreign AddIns apart.

    Being shipped in the install dir is the reliable sign of official content. The
    `BioWare="1"` attribute is only self-declared, but it is all there is for DLC that was
    downloaded into the user dir (non-Ultimate editions). Our own mods are ruled out first,
    so a mod claiming to be BioWare content is not mistaken for DLC.
    """
    if addin.uid.casefold() in {uid.casefold() for uid in managed}:
        return MANAGED
    if addin.uid.casefold() in shipped or addin.bioware:
        return OFFICIAL
    return EXTERNAL


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
    # The game treats UIDs case-insensitively: "MyAddIn" and "myaddin" are the same AddIn.
    present = {(item.get("UID") or "").casefold() for item in root.iter("AddInItem")}
    added = []
    for addin in addins:
        if addin.uid.casefold() in present:
            continue
        root.append(copy.deepcopy(addin.element))
        present.add(addin.uid.casefold())
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
    wanted = {uid.casefold() for uid in uids}
    for item in list(root.findall("AddInItem")):
        if (item.get("UID") or "").casefold() in wanted:
            root.remove(item)
            removed.append(item.get("UID"))
    if removed:
        _save(addins_xml, root)
    return removed
