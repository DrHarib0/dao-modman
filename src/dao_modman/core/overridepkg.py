"""Override packages: the `.override` format introduced by DAO-Modmanager.

A package is a zip holding a `Manifest.xml` (`<OverrideList>`) and a payload below
`BioWare/Dragon Age/`, i.e. relative to the user's Documents folder. It may carry an
`OverrideConfig.xml` describing options: for one game file, several variant files of which
exactly one is to be used.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path

MANIFEST = "manifest.xml"
CONFIG = "overrideconfig.xml"
PAYLOAD = ("bioware", "dragon age")  # the folders leading to the game user dir


class OverridePkgError(Exception):
    pass


@dataclass
class Package:
    name: str
    version: str = ""
    publisher: str = ""
    url: str = ""


@dataclass
class OptionValue:
    label: str
    file: str  # name of the variant file, somewhere in the package


@dataclass
class OptionKey:
    """One choice: which variant file stands in for `original`."""

    section: str
    name: str
    default: str
    original: str  # name of the game file the variants replace
    values: list[OptionValue] = field(default_factory=list)

    @property
    def id(self) -> str:
        return f"{self.section}/{self.name}"

    def value(self, label: str | None) -> OptionValue | None:
        """The value with this label, ignoring case (packages are sloppy about it)."""
        if label is None:
            return None
        wanted = label.casefold()
        return next((value for value in self.values if value.label.casefold() == wanted), None)

    def chosen(self, choices: dict[str, str]) -> OptionValue | None:
        """The user's choice if it is still valid, else the package default."""
        stored = {key.casefold(): label for key, label in choices.items()}
        return self.value(stored.get(self.id.casefold())) or self.value(self.default)

    def is_default(self, choices: dict[str, str]) -> bool:
        return self.chosen(choices) is self.value(self.default)


def roots(files: list[str]) -> dict[str, str]:
    """Override packages in a list of relative paths: folder -> path of its Manifest.xml.

    A folder is a package root if it holds a `Manifest.xml` and a `BioWare/Dragon Age/`
    folder (any casing). The top level is the folder `""`: a plain extracted package.
    """
    manifests: dict[str, str] = {}
    with_payload: set[str] = set()
    for rel in files:
        parts = rel.split("/")
        if parts[-1].casefold() == MANIFEST:
            manifests["/".join(parts[:-1])] = rel
        folded = [part.casefold() for part in parts[:-1]]
        for depth in range(len(folded) - 1):
            if tuple(folded[depth : depth + 2]) == PAYLOAD:
                with_payload.add("/".join(parts[:depth]))
    return {folder: manifests[folder] for folder in sorted(manifests) if folder in with_payload}


def target(root: str, rel: str) -> str | None:
    """Where a package file goes, relative to the game user dir; None if it is not payload."""
    parts = rel[len(root) + 1 if root else 0 :].split("/")
    if len(parts) < 3 or tuple(part.casefold() for part in parts[:2]) != PAYLOAD:
        return None  # Manifest.xml and other extras next to the payload
    if parts[-1].casefold() == CONFIG:
        return None  # read by mod managers, never by the game
    return "/".join(parts[2:])


def _parse(path: Path) -> ET.Element:
    try:
        return ET.parse(path).getroot()
    except (ET.ParseError, OSError) as exc:
        raise OverridePkgError(f"cannot read {path}: {exc}") from exc


def parse_manifest(manifest: Path) -> Package:
    item = _parse(manifest).find("OverrideItem")
    if item is None:
        raise OverridePkgError(f"{manifest}: no OverrideItem")

    def text(tag: str) -> str:
        # item can never be None; the closure is never called then
        # noinspection unresolved-references
        return (item.findtext(tag) or "").strip()

    return Package(
        name=text("Title") or (item.get("Name") or "").strip(),
        version=text("Version"),
        publisher=text("Publisher"),
        url=text("URL"),
    )


def parse_config(config: Path) -> list[OptionKey]:
    """The options of an `OverrideConfig.xml`. Elements are numbered: Section_0, Key_1, ..."""
    keys = []
    for section in _parse(config):
        if not section.tag.startswith("Section"):
            continue
        for key in section:
            if not key.tag.startswith("Key") or not key.get("OriginalFile"):
                continue
            values = [
                OptionValue(value.get("Value", "").strip(), value.get("OptionsFile", "").strip())
                for value in key
                if value.tag.startswith("Value") and value.get("OptionsFile")
            ]
            keys.append(
                OptionKey(
                    section=(section.get("Name") or section.tag).strip(),
                    name=(key.get("Name") or key.tag).strip(),
                    default=(key.get("DefaultValue") or "").strip(),
                    original=key.get("OriginalFile", "").strip(),
                    values=values,
                )
            )
    return keys


def apply_options(
    mapping: dict[str, str],
    keys: list[OptionKey],
    choices: dict[str, str],
    root: str,
    folder: str,
) -> tuple[dict[str, str], list[str]]:
    """Resolve a package's options on a `target -> source` mapping.

    For every key the chosen variant is deployed at the original file's path, and no variant
    is deployed under its own name. `root` is the package's folder inside the mod's files,
    `folder` the target folder of its OverrideConfig.xml. Returns `(mapping, warnings)`.

    Everything is looked up in the mapping as it came in: packages reuse one variant file
    for several options, so resolving one option must not take a file away from the next.
    """
    prefix = f"{root}/" if root else ""
    by_name: dict[str, list[str]] = {}  # file name -> targets of the package's files
    for dst, source in sorted(mapping.items()):
        if source.startswith(prefix):
            by_name.setdefault(source.rpartition("/")[2].casefold(), []).append(dst)

    variants = {value.file.casefold() for key in keys for value in key.values}
    result = {
        target: source
        for target, source in mapping.items()
        if not (source.startswith(prefix) and source.rpartition("/")[2].casefold() in variants)
    }
    warnings: list[str] = []
    for key in keys:
        chosen = key.chosen(choices)
        if chosen is None:
            continue  # nothing selectable: the original, if any, stays as it is
        originals = [
            target
            for target in by_name.get(key.original.casefold(), [])
            if key.original.casefold() not in variants
        ]
        sources = by_name.get(chosen.file.casefold(), [])
        if not sources:
            # Packages often ship the default only as the original file, not as a variant.
            if not (originals and chosen is key.value(key.default)):
                warnings.append(f"option {key.id}: variant file {chosen.file!r} is missing")
            continue
        for target in originals or [f"{folder}/{key.original}"]:
            result[target] = mapping[sources[0]]
    return result, warnings
