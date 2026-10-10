"""dazip `Manifest.xml` parsing."""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

MANIFEST = "manifest.xml"
CONTENTS = "contents"


class DazipError(Exception):
    pass


@dataclass
class AddIn:
    uid: str
    name: str
    version: str
    element: ET.Element


def find_manifest(files_dir: Path) -> Path | None:
    """The top-level Manifest.xml of an extracted dazip (any casing)."""
    if not files_dir.is_dir():
        return None
    for entry in files_dir.iterdir():
        if entry.name.casefold() == MANIFEST and entry.is_file():
            return entry
    return None


def is_dazip(files_dir: Path) -> bool:
    has_contents = files_dir.is_dir() and any(
        entry.name.casefold() == CONTENTS and entry.is_dir() for entry in files_dir.iterdir()
    )
    return has_contents and find_manifest(files_dir) is not None


def parse_manifest(manifest: Path) -> list[AddIn]:
    try:
        root = ET.parse(manifest).getroot()
    except (ET.ParseError, OSError) as exc:
        raise DazipError(f"cannot read {manifest}: {exc}") from exc
    addins = []
    for item in root.iter("AddInItem"):
        uid = item.get("UID")
        if not uid:
            continue
        title = item.find("Title")
        name = (title.get("DefaultText") if title is not None else None) or item.get("Name") or uid
        addins.append(AddIn(uid=uid, name=name, version=item.get("Version", ""), element=item))
    return addins
