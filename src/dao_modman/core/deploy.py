"""Deployment: plan (ops, priority, conflicts, merges), apply to the game user dir, unapply."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import addins, chargen
from .archive import sha256_file
from .dazip import AddIn
from .library import Library
from .metadata import write_atomic
from .models import Mod
from .paths import normalize_rel, resolve_case

_CHUNK = 1024 * 1024


class DeployError(Exception):
    pass


APPLYING = "applying"  # intent written, files being copied: to be rolled back if found
APPLIED = "applied"

# Called as progress(what is being done, items done, items in total).
Progress = Callable[[str, int, int], None]


MERGED = "(merged)"


@dataclass
class PlannedFile:
    target: str
    source: Path
    mod_id: str
    content: bytes | None = None  # generated file: written instead of copying `source`


OPEN = "open"  # an ordinary conflict
ACCEPTED = "accepted"  # the winner declares these overwrites as intended
ERROR = "error"  # a declared overwrite does not happen, because of the load order


@dataclass
class Provider:
    """One mod's copies of a contested file."""

    mod_id: str
    targets: list[str]


@dataclass
class Conflict:
    """A file that several enabled mods ship with different content."""

    name: str  # the file name below an override dir, else the full target path
    providers: list[Provider]  # in load order: the last one wins
    status: str = OPEN
    # (declaring mod, other mod): declared to overwrite the other, but loads before it
    misordered: list[tuple[str, str]] = field(default_factory=list)

    @property
    def mod_ids(self) -> list[str]:
        return [provider.mod_id for provider in self.providers]

    @property
    def winner(self) -> Provider:
        return self.providers[-1]


@dataclass
class Plan:
    files: list[PlannedFile] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    # generated target -> mod ids whose files were merged into it
    merged: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    addins: list[AddIn] = field(default_factory=list)

    def with_status(self, status: str) -> list[Conflict]:
        return [conflict for conflict in self.conflicts if conflict.status == status]

    def conflicting_mods(self) -> set[str]:
        """Mods that take part in an open conflict."""
        return {mod_id for conflict in self.with_status(OPEN) for mod_id in conflict.mod_ids}

    def misordered_mods(self) -> set[str]:
        """Mods whose declared overwrite is defeated by the load order."""
        return {
            mod_id
            for conflict in self.with_status(ERROR)
            for pair in conflict.misordered
            for mod_id in pair
        }


@dataclass
class ApplyResult:
    written: int = 0
    skipped: list[str] = field(default_factory=list)  # already existed, left alone
    addins: list[str] = field(default_factory=list)


@dataclass
class UnapplyResult:
    removed: int = 0
    kept: list[str] = field(default_factory=list)  # modified since deployment
    addins: list[str] = field(default_factory=list)


def _override_scope(target: str) -> tuple[str, str] | None:
    """`(override dir, file name)` for files below an `override` directory.

    The game looks resources up by file name only, in every subfolder of an override
    directory, so two files with the same name conflict wherever they are placed.
    """
    parts = target.split("/")
    folded = [part.casefold() for part in parts[:-1]]
    if "override" not in folded:
        return None
    return "/".join(parts[: folded.index("override") + 1]), parts[-1]


def _mod_ids(items: list[PlannedFile]) -> list[str]:
    return list(dict.fromkeys(item.mod_id for item in items))


def _conflict(items: list[PlannedFile]) -> Conflict:
    """Describe a clash; `items` are all copies of one file in load order."""
    scope = _override_scope(items[-1].target)
    providers = [
        Provider(mod_id, [item.target for item in items if item.mod_id == mod_id])
        for mod_id in _mod_ids(items)
    ]
    return Conflict(scope[1] if scope else items[-1].target, providers)


_hashes: dict[tuple[Path, int, int], str] = {}


def _classify(conflict: Conflict, mods: dict[str, Mod]) -> None:
    """Set a conflict's status from the overwrites its mods declare."""
    ids, name = conflict.mod_ids, conflict.name
    conflict.misordered = [
        (early, late)
        for index, early in enumerate(ids)
        for late in ids[index + 1 :]
        if mods[early].may_overwrite(late, name)
    ]
    if conflict.misordered:
        conflict.status = ERROR
    elif all(mods[ids[-1]].may_overwrite(loser, name) for loser in ids[:-1]):
        conflict.status = ACCEPTED
    else:
        conflict.status = OPEN


def _identical(items: list[PlannedFile]) -> bool:
    """True if all files have the same content, so it does not matter which one is used."""
    try:
        stats = [(item.source, item.source.stat()) for item in items]
        if len({stat.st_size for _source, stat in stats}) > 1:
            return False
        digests = set()
        for source, stat in stats:
            key = (source, stat.st_size, stat.st_mtime_ns)  # plans are rebuilt often
            if key not in _hashes:
                _hashes[key] = sha256_file(source)
            digests.add(_hashes[key])
    except OSError:
        return False
    return len(digests) == 1


def build_plan(library: Library) -> Plan:
    plan = Plan()
    candidates: dict[tuple[str, ...], list[PlannedFile]] = {}
    chargens: dict[str, list[PlannedFile]] = {}
    for entry, mod in library.ordered():
        if not entry.enabled:
            continue
        files_dir = library.files_dir(mod.id)
        targets = library.targets(mod, plan.warnings)
        for target, source in targets.items():
            planned = PlannedFile(normalize_rel(target), files_dir / source, mod.id)
            scope = _override_scope(planned.target)
            if scope is None:
                key: tuple[str, ...] = (planned.target.casefold(),)
            elif scope[1].casefold() == chargen.FILE_NAME:
                chargens.setdefault(scope[0].casefold(), []).append(planned)
                continue
            else:
                key = (scope[0].casefold(), scope[1].casefold())
            candidates.setdefault(key, []).append(planned)
        plan.addins += library.addins(mod, targets)

    for items in candidates.values():
        # Only the last mod's files are deployed; duplicates inside one mod are its business.
        winner = items[-1]
        plan.files += [item for item in items if item.mod_id == winner.mod_id]
        if len(_mod_ids(items)) > 1 and not _identical(items):
            plan.conflicts.append(_conflict(items))

    for items in chargens.values():
        mod_ids = _mod_ids(items)
        if len(mod_ids) == 1:
            plan.files += items
            continue
        content, failed = chargen.merge([item.source for item in items])
        plan.warnings += [
            f"{item.mod_id}: cannot parse {item.target}, left out of the merge"
            for item in items
            if item.source in failed
        ]
        if content is None:
            plan.files.append(items[-1])
            plan.conflicts.append(_conflict(items))
            continue
        scope_dir = _override_scope(items[0].target)[0]
        target = f"{scope_dir}/{chargen.FILE_NAME}"
        plan.files.append(PlannedFile(target, items[-1].source, MERGED, content))
        plan.merged[target] = mod_ids

    plan.files.sort(key=lambda planned: planned.target.casefold())
    plan.conflicts.sort(key=lambda conflict: conflict.name.casefold())
    for conflict in plan.conflicts:
        _classify(conflict, library.mods)
    return plan


def load_record(deployed_file: Path) -> dict | None:
    if not deployed_file.is_file():
        return None
    try:
        record = json.loads(deployed_file.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DeployError(f"cannot read {deployed_file}: {exc}") from exc
    if not isinstance(record, dict) or "user_dir" not in record:
        raise DeployError(f"{deployed_file}: not a deployment record")
    return record


def _save_record(deployed_file: Path, record: dict) -> None:
    write_atomic(deployed_file, json.dumps(record, indent=1).encode())


def _write_hashed(planned: PlannedFile, dest: Path) -> str:
    digest = hashlib.sha256()
    # "xb": never overwrite a file that appeared in the meantime
    with dest.open("xb") as out:
        if planned.content is not None:
            digest.update(planned.content)
            out.write(planned.content)
            return digest.hexdigest()
        with planned.source.open("rb") as src:
            while chunk := src.read(_CHUNK):
                digest.update(chunk)
                out.write(chunk)
    return digest.hexdigest()


class _Resolver:
    """Maps target paths to real paths, reusing the casing of what exists or is planned.

    Like `resolve_case`, but it also remembers folders that are only about to be created, so
    that `Packages/a` and `packages/b` end up in one folder even before either exists.
    """

    def __init__(self, user_dir: Path) -> None:
        self.user_dir = user_dir
        self._children: dict[Path, dict[str, Path]] = {}

    def _listing(self, directory: Path) -> dict[str, Path]:
        if directory not in self._children:
            entries = directory.iterdir() if directory.is_dir() else []
            self._children[directory] = {entry.name.casefold(): entry for entry in entries}
        return self._children[directory]

    def resolve(self, target: str) -> Path:
        current = self.user_dir
        for part in target.split("/"):
            current = self._listing(current).setdefault(part.casefold(), current / part)
        return current


def _missing_dirs(user_dir: Path, paths: list[Path]) -> list[str]:
    """Folders that have to be created for `paths`, parents first."""
    missing: dict[Path, None] = {}
    for path in paths:
        chain = []
        current = path.parent
        while current != user_dir and not current.exists() and current not in missing:
            chain.append(current)
            current = current.parent
        missing.update(dict.fromkeys(reversed(chain)))
    return [path.relative_to(user_dir).as_posix() for path in missing]


def _undo(record: dict) -> None:
    """Remove everything an unfinished apply set out to create.

    Every listed path was absent when that run started, so nothing foreign is touched.
    """
    user_dir = Path(record["user_dir"])
    for item in record.get("files", []):
        path = user_dir / normalize_rel(item["path"])
        if path.is_file() or path.is_symlink():
            path.unlink()
    for rel in sorted(record.get("dirs", []), key=lambda d: d.count("/"), reverse=True):
        with contextlib.suppress(OSError):  # not empty or never created
            (user_dir / normalize_rel(rel)).rmdir()
    if uids := record.get("addins"):
        with contextlib.suppress(ET.ParseError, OSError, ValueError):
            addins.unregister(resolve_case(user_dir, addins.ADDINS_REL), uids)


def rollback(deployed_file: Path) -> bool:
    """Undo an apply that was cut short (process killed). True if there was one."""
    record = load_record(deployed_file)
    if record is None or record.get("state") != APPLYING:
        return False
    _undo(record)
    deployed_file.unlink()
    return True


def apply(
    plan: Plan, user_dir: Path, deployed_file: Path, progress: Progress | None = None
) -> ApplyResult:
    """Replace the current deployment (if any) with `plan`, all or nothing.

    The intent is written down before the first file is copied. If anything fails, what this
    run created is removed again and nothing is applied; if the process dies, `rollback`
    does the same on the next start.
    """
    if not user_dir.is_dir():
        raise DeployError(f"game user dir not found: {user_dir}")
    unapply(deployed_file, progress)

    result = ApplyResult()
    resolver = _Resolver(user_dir)
    writes: dict[Path, PlannedFile] = {}
    for planned in plan.files:
        dest = resolver.resolve(planned.target)
        if dest in writes or dest.exists() or dest.is_symlink():
            result.skipped.append(planned.target)
        else:
            writes[dest] = planned
    addins_xml = resolve_case(user_dir, addins.ADDINS_REL)
    try:
        new_addins = addins.absent(addins_xml, plan.addins)
    except (ET.ParseError, OSError, ValueError) as exc:
        raise DeployError(f"cannot read {addins_xml}: {exc}") from exc

    files = [
        {"path": dest.relative_to(user_dir).as_posix(), "mod": planned.mod_id}
        for dest, planned in writes.items()
    ]
    record: dict = {
        "state": APPLYING,
        "user_dir": str(user_dir),
        "files": files,
        "dirs": _missing_dirs(user_dir, [*writes, addins_xml] if new_addins else list(writes)),
        "addins": new_addins,
    }
    _save_record(deployed_file, record)
    try:
        for index, (dest, planned) in enumerate(writes.items()):
            dest.parent.mkdir(parents=True, exist_ok=True)
            files[index]["sha256"] = _write_hashed(planned, dest)
            if progress:
                progress("Copying files", index + 1, len(writes))
        if new_addins:
            addins_xml.parent.mkdir(parents=True, exist_ok=True)
            addins.register(addins_xml, plan.addins)
    except BaseException as exc:
        _undo(record)
        deployed_file.unlink(missing_ok=True)
        if isinstance(exc, Exception):
            raise DeployError(f"apply failed and was rolled back: {exc}") from exc
        raise
    record["state"] = APPLIED
    _save_record(deployed_file, record)
    result.written, result.addins = len(writes), new_addins
    return result


def unapply(deployed_file: Path, progress: Progress | None = None) -> UnapplyResult:
    """Remove what the last apply wrote, leaving modified files alone."""
    result = UnapplyResult()
    if rollback(deployed_file):
        return result
    record = load_record(deployed_file)
    if record is None:
        return result
    user_dir = Path(record["user_dir"])
    items = record.get("files", [])
    for index, item in enumerate(items):
        path = user_dir / normalize_rel(item["path"])
        if not path.is_symlink() and path.is_file():
            if sha256_file(path) == item["sha256"]:
                path.unlink()
                result.removed += 1
            else:
                result.kept.append(item["path"])
        if progress:
            progress("Removing applied files", index + 1, len(items))
    for rel in sorted(record.get("dirs", []), key=lambda d: d.count("/"), reverse=True):
        with contextlib.suppress(OSError):  # not empty or already gone
            (user_dir / normalize_rel(rel)).rmdir()
    if uids := record.get("addins"):
        addins_xml = resolve_case(user_dir, addins.ADDINS_REL)
        result.addins = addins.unregister(addins_xml, uids)
    deployed_file.unlink()
    return result


# The places in the game's user dir that hold nothing but mods.
MOD_DIRS = ("packages", "AddIns")
KEPT_DIRS = ("packages/core/override", "AddIns")  # empty folders the game expects


@dataclass
class Foreign:
    """What is in the mod directories without being an unchanged file of ours."""

    unmanaged: list[str] = field(default_factory=list)  # files we have no record of
    modified: list[str] = field(default_factory=list)  # applied by us, changed since
    addins: list[str] = field(default_factory=list)  # AddIns registered by something else

    def __bool__(self) -> bool:
        return bool(self.unmanaged or self.modified or self.addins)


def _external_addins(user_dir: Path, install_dir: Path | None, ours: set[str]) -> list[str]:
    shipped = addins.shipped_uids(install_dir)
    return [
        addin.uid
        for addin in addins.list_registered(user_dir)
        if addins.classify(addin, ours, shipped) == addins.EXTERNAL
    ]


def _official_dirs(user_dir: Path, install_dir: Path | None) -> set[str]:
    """Folders of official DLC inside the user dir (editions that download DLC there)."""
    shipped = addins.shipped_uids(install_dir)
    return {
        f"addins/{addin.uid.casefold()}"
        for addin in addins.list_registered(user_dir)
        if addins.classify(addin, set(), shipped) == addins.OFFICIAL
    }


def _mod_dir_entries(user_dir: Path, protected: set[str]) -> list[tuple[str, Path]]:
    """Files (and links, which are never followed) below the mod directories."""
    found = []
    for name in MOD_DIRS:
        top = resolve_case(user_dir, name)
        if top.is_symlink() or not top.is_dir():
            continue
        for root, dirnames, filenames in os.walk(top):
            links = [name for name in dirnames if Path(root, name).is_symlink()]
            for entry in [*filenames, *links]:
                path = Path(root, entry)
                rel = path.relative_to(user_dir).as_posix()
                if not any(f"{rel.casefold()}/".startswith(f"{folder}/") for folder in protected):
                    found.append((rel, path))
    return sorted(found)


def unmanaged(
    user_dir: Path, deployed_file: Path, install_dir: Path | None = None
) -> list[tuple[str, Path]]:
    """Files in the mod directories that the deployment record does not list."""
    record = load_record(deployed_file) or {}
    applied = {item["path"].casefold() for item in record.get("files", [])}
    entries = _mod_dir_entries(user_dir, _official_dirs(user_dir, install_dir))
    return [(rel, path) for rel, path in entries if rel.casefold() not in applied]


def scan(
    user_dir: Path,
    deployed_file: Path,
    install_dir: Path | None = None,
    progress: Progress | None = None,
) -> Foreign:
    """Find what a plain unapply would leave behind in the mod directories."""
    record = load_record(deployed_file) or {}
    applied = {item["path"].casefold(): item.get("sha256") for item in record.get("files", [])}
    foreign = Foreign()
    entries = _mod_dir_entries(user_dir, _official_dirs(user_dir, install_dir))
    for index, (rel, path) in enumerate(entries):
        if rel.casefold() not in applied:
            foreign.unmanaged.append(rel)
        elif path.is_symlink() or sha256_file(path) != applied[rel.casefold()]:
            foreign.modified.append(rel)
        if progress:
            progress("Checking files", index + 1, len(entries))
    foreign.addins = _external_addins(user_dir, install_dir, set(record.get("addins", [])))
    return foreign


def clean(user_dir: Path, deployed_file: Path, install_dir: Path | None = None) -> int:
    """Empty the mod directories of everything but official DLC. Unapply first.

    Removes files installed by hand or by other tools, and the AddIns they registered.
    Returns the number of files removed.
    """
    if load_record(deployed_file) is not None:
        raise DeployError("unapply before cleaning the mod directories")
    protected = _official_dirs(user_dir, install_dir)
    entries = _mod_dir_entries(user_dir, protected)
    for _rel, path in entries:
        path.unlink()
    kept = {resolve_case(user_dir, rel) for rel in KEPT_DIRS}
    for name in MOD_DIRS:
        top = resolve_case(user_dir, name)
        if top.is_symlink() or not top.is_dir():
            continue
        for root, _dirnames, _filenames in os.walk(top, topdown=False):
            if Path(root) not in kept and Path(root) != top:
                with contextlib.suppress(OSError):  # not empty: official DLC inside
                    Path(root).rmdir()
    if external := _external_addins(user_dir, install_dir, set()):
        addins.unregister(resolve_case(user_dir, addins.ADDINS_REL), external)
    return len(entries)
