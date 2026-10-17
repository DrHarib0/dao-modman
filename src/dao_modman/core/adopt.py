"""Adopt: take files that were installed without dao-modman into the library.

Someone who starts using dao-modman on an already modded game has mods in the game's user
dir that the library knows nothing about. Adopting turns them into library mods: each group
of files (an override folder, an AddIn) is packed into an archive in `downloads/`, in a
layout the library already understands, and imported like any other archive. Every file
then deploys to exactly where it was:

- override files become an ordinary mod of loose files; a rename path operation puts them
  back into their folder if it is not named like the mod;
- an AddIn registered in `Settings/AddIns.xml` becomes a dazip, with its registration as the
  manifest, so apply registers it again;
- everything else becomes an override package (`BioWare/Dragon Age/<path>`), the only layout
  that can place files outside the override folder.

Nothing in the game dir is touched until every group is imported and every library copy is
checked against its original. Only then are the originals removed and the AddIns
unregistered; the next apply puts everything back, now managed.
"""

from __future__ import annotations

import contextlib
import copy
import xml.etree.ElementTree as ET
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import addins, deploy, pathops
from .archive import sha256_file
from .dazip import AddIn
from .library import Library, LibraryError, slugify
from .models import OP_RENAME, Mod, PathOp
from .paths import resolve_case

OVERRIDE = ("packages", "core", "override")
LOOSE_NAME = "Loose override files"
OTHER_NAME = "Other packages files"
ARCHIVE_PREFIX = "adopted-"
PAYLOAD = "BioWare/Dragon Age"  # where an override package keeps its files
DESCRIPTION = "Adopted by dao-modman from the game's user dir"

Progress = Callable[[str, int, int], None]


class AdoptError(Exception):
    pass


@dataclass
class Group:
    """Files that become one mod."""

    name: str
    files: list[tuple[str, Path]] = field(default_factory=list)  # (path below user dir, file)
    addin: AddIn | None = None  # a registered AddIn: the group becomes a dazip
    folder: str | None = None  # override files: the folder they are in, below the user dir
    # ignored files (readmes, Thumbs.db): taken along into the library, never deployed
    extras: list[tuple[str, Path]] = field(default_factory=list)


@dataclass
class Found:
    groups: list[Group] = field(default_factory=list)
    links: list[str] = field(default_factory=list)  # symbolic links: never adopted
    ignored: list[str] = field(default_factory=list)  # ignored, with no group to go with

    @property
    def file_count(self) -> int:
        return sum(len(group.files) for group in self.groups)


@dataclass
class Result:
    mods: list[Mod] = field(default_factory=list)
    removed: int = 0  # originals removed from the game dir
    unregistered: list[str] = field(default_factory=list)  # AddIn UIDs


def find(
    library: Library, user_dir: Path, deployed_file: Path, install_dir: Path | None = None
) -> Found:
    """The files in the mod directories that dao-modman does not manage, as groups."""
    external = {
        addin.uid.casefold(): addin
        for addin in addins.list_registered(user_dir)
        if addins.classify(addin, set(), addins.shipped_uids(install_dir)) == addins.EXTERNAL
    }
    found = Found()
    groups: dict[tuple[str, ...], Group] = {}
    extras: dict[tuple[str, ...], list[tuple[str, Path]]] = {}
    for rel, path in deploy.unmanaged(user_dir, deployed_file, install_dir):
        if path.is_symlink():
            found.links.append(rel)
            continue
        parts = rel.split("/")
        folded = tuple(part.casefold() for part in parts)
        folder = None
        if folded[:3] == OVERRIDE and len(parts) > 4:
            key, name, addin = ("override", folded[3]), parts[3], None
            folder = "/".join(parts[:4])
        elif folded[:3] == OVERRIDE:
            key, name, addin = ("loose",), LOOSE_NAME, None
            folder = "/".join(parts[:3])
        elif folded[0] == "addins" and len(parts) > 2:
            key, name, addin = ("addin", folded[1]), parts[1], external.get(folded[1])
        else:
            key, name, addin = ("other",), OTHER_NAME, None
        if library.ignore.matches(rel):
            extras.setdefault(key, []).append((rel, path))
            continue
        group = groups.setdefault(key, Group(name, addin=addin, folder=folder))
        group.files.append((rel, path))
    for key, files in extras.items():
        if key in groups:  # e.g. the readme of an adopted folder: it goes along
            groups[key].extras = files
        else:
            found.ignored += [rel for rel, _path in files]
    found.groups = list(groups.values())
    return found


def _archive_path(library: Library, name: str) -> Path:
    base = f"{ARCHIVE_PREFIX}{slugify(name)}"
    candidate, counter = library.dirs.downloads / f"{base}.zip", 2
    while candidate.exists():
        candidate = library.dirs.downloads / f"{base}-{counter}.zip"
        counter += 1
    return candidate


def _manifest(group: Group) -> bytes:
    if group.addin is not None:
        root = ET.Element("Manifest", Type="AddIn")
        ET.SubElement(root, "AddInsList").append(copy.deepcopy(group.addin.element))
    else:
        root = ET.Element("OverrideList")
        item = ET.SubElement(root, "OverrideItem", Name=group.name, Enabled="1")
        ET.SubElement(item, "Title").text = group.name
        ET.SubElement(item, "Description").text = DESCRIPTION
    ET.indent(root)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def pack(group: Group, archive: Path) -> None:
    """Write the group as loose override files, a dazip or an override package."""
    with zipfile.ZipFile(archive, "x") as zf:  # stored: the files are often compressed already
        if group.folder is not None:
            for rel, path in [*group.files, *group.extras]:
                zf.write(path, rel[len(group.folder) + 1 :])
            return
        prefix = "Contents" if group.addin is not None else PAYLOAD
        zf.writestr("Manifest.xml", _manifest(group))
        for rel, path in [*group.files, *group.extras]:
            zf.write(path, f"{prefix}/{rel}")


def _place(library: Library, mod: Mod, group: Group) -> None:
    """Send loose override files back to their folder if it is not the mod's own."""
    own = f"{pathops.OVERRIDE_DIR}/{mod.id}"
    if group.folder is not None and group.folder.casefold() != own.casefold():
        mod.ops = [PathOp(OP_RENAME, own, group.folder)]
        library.save_mod(mod, f"{mod.id}: keep the files in {group.folder}")


def _verify(library: Library, mod: Mod, group: Group) -> None:
    """Every original must deploy from a library file with the same content."""
    targets = {target.casefold(): source for target, source in library.targets(mod).items()}
    files_dir = library.files_dir(mod.id)
    for rel, path in group.files:
        source = targets.get(rel.casefold())
        if source is None:
            raise AdoptError(f"{mod.name}: {rel} would not be installed again")
        if sha256_file(files_dir / source) != sha256_file(path):
            raise AdoptError(f"{mod.name}: the library copy of {rel} differs")


def _remove_originals(user_dir: Path, groups: list[Group]) -> int:
    removed = 0
    folders: set[Path] = set()
    for group in groups:
        for _rel, path in [*group.files, *group.extras]:
            path.unlink()
            removed += 1
            folders.update(path.parents)
    kept = {resolve_case(user_dir, rel) for rel in deploy.KEPT_DIRS}
    tops = {resolve_case(user_dir, name) for name in deploy.MOD_DIRS}
    for folder in sorted(folders, key=lambda p: len(p.parts), reverse=True):
        if folder in kept or folder in tops or not folder.is_relative_to(user_dir):
            continue
        if not any(folder.is_relative_to(top) for top in tops):
            continue
        with contextlib.suppress(OSError):  # not empty
            folder.rmdir()
    return removed


def adopt(
    library: Library,
    user_dir: Path,
    found: Found,
    progress: Progress | None = None,
) -> Result:
    """Import every group as a mod, check the copies, then remove the originals.

    If importing or checking fails, the mods and archives made so far are removed again and
    the game dir is left as it was.
    """
    result = Result()
    archives: list[Path] = []
    total = len(found.groups)
    try:
        for index, group in enumerate(found.groups):
            if progress:
                progress(f"Adopting {group.name}", index, total)
            archive = _archive_path(library, group.name)
            archives.append(archive)
            pack(group, archive)
            name = None if group.addin is not None else group.name  # dazips: the AddIn's title
            mod = library.import_archive(archive, name=name)
            result.mods.append(mod)
            _place(library, mod, group)
            _verify(library, mod, group)
    except (AdoptError, LibraryError, OSError, zipfile.BadZipFile) as exc:
        for mod in result.mods:
            with contextlib.suppress(OSError, KeyError):
                library.remove(mod.id)
        for archive in archives:
            archive.unlink(missing_ok=True)
        raise AdoptError(f"nothing adopted: {exc}") from exc
    if progress:
        progress("Removing the originals", total, total)
    result.removed = _remove_originals(user_dir, found.groups)
    uids = [group.addin.uid for group in found.groups if group.addin is not None]
    if uids:
        result.unregistered = addins.unregister(resolve_case(user_dir, addins.ADDINS_REL), uids)
    return result


def describe(found: Found) -> str:
    """What adopting would do, for a confirmation dialog."""
    lines = [
        f"{found.file_count} file(s) in the mod directories are not managed by dao-modman.",
        "Adopting turns each group into a mod in the library:",
        "",
    ]
    for group in found.groups:
        kind = "AddIn" if group.addin is not None else "files"
        label = group.addin.name if group.addin is not None else group.name
        lines.append(f"  {label} ({kind}, {len(group.files)} file(s))")
    if extras := sum(len(group.extras) for group in found.groups):
        lines.append(f"\n{extras} ignored file(s) (readmes etc.) go along, not to be installed.")
    if found.links:
        lines.append(f"{len(found.links)} symbolic link(s) are left alone.")
    if found.ignored:
        lines.append(f"{len(found.ignored)} other ignored file(s) are left alone.")
    lines += [
        "",
        "The originals are removed once the library copies are checked; apply installs",
        "them again, now managed. The archives stay in downloads/.",
    ]
    return "\n".join(lines)
