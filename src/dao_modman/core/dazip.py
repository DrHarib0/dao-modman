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
    enabled: bool = True
    bioware: bool = False
    extends: str = ""  # ExtendedModuleUID: "Single Player", another AddIn's UID or empty


def addin_from_element(item: ET.Element) -> AddIn | None:
    """Describe an `<AddInItem>` as found in a dazip manifest or the game's addins.xml."""
    uid = item.get("UID")
    if not uid:
        return None
    title = item.find("Title")
    name = (title.get("DefaultText") if title is not None else None) or item.get("Name") or uid
    return AddIn(
        uid=uid,
        name=name.strip(),
        version=(item.findtext("Version") or item.get("Version") or "").strip(),
        element=item,
        enabled=item.get("Enabled", "1") != "0",
        bioware=item.get("BioWare") == "1",
        extends=item.get("ExtendedModuleUID") or "",
    )


def roots(files: list[str]) -> dict[str, str]:
    """Dazips found in a list of relative paths: folder -> path of its Manifest.xml.

    A folder is a dazip root if it holds a `Manifest.xml` and a `Contents/` folder (any
    casing). The top level is the folder `""`: a plain extracted dazip.
    """
    manifests: dict[str, str] = {}
    with_contents: set[str] = set()
    for rel in files:
        parts = rel.split("/")
        if parts[-1].casefold() == MANIFEST:
            manifests["/".join(parts[:-1])] = rel
        for depth, part in enumerate(parts[:-1]):
            if part.casefold() == CONTENTS:
                with_contents.add("/".join(parts[:depth]))
    return {folder: manifests[folder] for folder in sorted(manifests) if folder in with_contents}


def owner(roots: dict[str, str], rel: str) -> str | None:
    """The dazip root that the file `rel` belongs to (the innermost one), if any."""
    inside = [folder for folder in roots if not folder or rel.startswith(folder + "/")]
    return max(inside, key=len, default=None)


def parse_manifest(manifest: Path) -> list[AddIn]:
    try:
        root = ET.parse(manifest).getroot()
    except (ET.ParseError, OSError) as exc:
        raise DazipError(f"cannot read {manifest}: {exc}") from exc
    return [addin for item in root.iter("AddInItem") if (addin := addin_from_element(item))]
