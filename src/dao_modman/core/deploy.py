"""Deployment: plan (ops, priority, conflicts, merges), apply to the game user dir, unapply."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, BinaryIO

from . import addins, chargen
from .archive import sha256_file
from .dazip import AddIn
from .library import Library
from .metadata import write_atomic
from .paths import normalize_rel, resolve_case

if TYPE_CHECKING:
    from .textures import Reduction

_CHUNK = 1024 * 1024


class DeployError(Exception):
    pass


APPLYING = "applying"  # intent written, files being copied: to be rolled back if found
APPLIED = "applied"

# Called as progress(what is being done, items done, items in total).
Progress = Callable[[str, int, int], None]


MERGED = "(merged)"
# a file in the mod directories that dao-modman did not install: it shows up in conflicts
UNMANAGED = "(not managed)"


@dataclass
class PlannedFile:
    target: str
    source: Path
    mod_id: str
    content: bytes | None = None  # generated file: written instead of copying `source`
    reduction: Reduction | None = None  # texture to be written smaller than `source`

    def signature(self) -> str:
        """What the plan wants at the target, before texture limits and item tables.

        A library file never changes in place (an update replaces it), so its path, size and
        modification time identify its content. Merged files are identified by their content.
        """
        if self.mod_id == MERGED and self.content is not None:
            return "content:" + hashlib.sha256(self.content).hexdigest()
        try:
            stat = self.source.stat()
        except OSError:
            return f"{self.mod_id}:{self.source}:missing"
        return f"{self.mod_id}:{self.source}:{stat.st_size}:{stat.st_mtime_ns}"


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
    # enabled mods that add nothing to the game: no file of theirs is deployed or merged
    no_effect: list[str] = field(default_factory=list)
    # mod id -> later mods whose identical copies of its files are deployed instead
    same_files: dict[str, set[str]] = field(default_factory=dict)

    def overwrites(self) -> dict[str, tuple[set[str], set[str]]]:
        """Per mod in a conflict: `(mods it overwrites, mods that overwrite it)`.

        Files not managed by dao-modman always win; they appear as `UNMANAGED`.
        """
        result: dict[str, tuple[set[str], set[str]]] = {}
        for conflict in self.conflicts:
            *losers, winner = conflict.mod_ids
            for loser in losers:
                result.setdefault(loser, (set(), set()))[1].add(winner)
                if winner != UNMANAGED:
                    result.setdefault(winner, (set(), set()))[0].add(loser)
        return result


@dataclass
class ApplyResult:
    written: int = 0
    removed: int = 0  # files of ours that are no longer wanted, or wanted differently
    unchanged: int = 0  # already applied as planned: not touched
    skipped: list[str] = field(default_factory=list)  # already existed, left alone
    kept: list[str] = field(default_factory=list)  # applied by us, changed since: left alone
    addins: list[str] = field(default_factory=list)  # registered by this run
    linked: int = 0  # of the written files, cloned or hard-linked instead of copied
    # mod id -> kept files that are hard links: their library copies were changed with them
    library_changed: dict[str, list[str]] = field(default_factory=dict)
    unregistered: list[str] = field(default_factory=list)  # of disabled mods


@dataclass
class UnapplyResult:
    removed: int = 0
    kept: list[str] = field(default_factory=list)  # modified since deployment
    addins: list[str] = field(default_factory=list)
    library_changed: dict[str, list[str]] = field(default_factory=dict)  # as in ApplyResult


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


def build_plan(
    library: Library,
    foreign: list[tuple[str, Path]] | None = None,
    progress: Progress | None = None,
) -> Plan:
    """What applying would deploy, and which files clash.

    `foreign` are the files in the mod directories that dao-modman does not manage (see
    `unmanaged`). They are never deployed or replaced, but a mod file with the same path or
    override name clashes with them, and they win: apply never overwrites a file it did not
    write, and which of two same-named override files the game uses is not up to us.
    """
    plan = Plan()
    candidates: dict[tuple[str, ...], list[PlannedFile]] = {}
    chargens: dict[str, list[PlannedFile]] = {}
    enabled = [(entry, mod) for entry, mod in library.ordered() if entry.enabled]
    with_addins: set[str] = set()
    for index, (_entry, mod) in enumerate(enabled):
        if progress:
            progress("Building the plan", index + 1, len(enabled))
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
        mod_addins = library.addins(mod, targets)
        plan.addins += mod_addins
        if mod_addins:
            with_addins.add(mod.id)
    for rel, path in foreign or []:
        planned = PlannedFile(normalize_rel(rel), path, UNMANAGED)
        scope = _override_scope(planned.target)
        key = (planned.target.casefold(),) if scope is None else tuple(map(str.casefold, scope))
        if key in candidates and not path.is_symlink():
            candidates[key].append(planned)

    for items in candidates.values():
        # Only the last mod's files are deployed; duplicates inside one mod are its business.
        winner = [item for item in items if item.mod_id != UNMANAGED][-1]
        plan.files += [item for item in items if item.mod_id == winner.mod_id]
        if len(_mod_ids(items)) > 1:
            if not _identical(items):
                plan.conflicts.append(_conflict(items))
                continue
            for mod_id in _mod_ids(items):
                if mod_id not in (winner.mod_id, UNMANAGED):
                    plan.same_files.setdefault(mod_id, set()).add(winner.mod_id)

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

    used = {planned.mod_id for planned in plan.files} | with_addins
    used.update(mod_id for mod_ids in plan.merged.values() for mod_id in mod_ids)
    plan.no_effect = [mod.id for _entry, mod in enabled if mod.id not in used]
    plan.files.sort(key=lambda planned: planned.target.casefold())
    plan.conflicts.sort(key=lambda conflict: conflict.name.casefold())
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


def _chunks(planned: PlannedFile) -> Iterator[bytes]:
    """The bytes a planned file is written as."""
    if planned.content is not None:
        yield planned.content
        return
    with planned.source.open("rb") as src:
        if planned.reduction is not None:
            yield planned.reduction.header
            src.seek(planned.reduction.offset)
        while chunk := src.read(_CHUNK):
            yield chunk


def _copy_into(planned: PlannedFile, out: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in _chunks(planned):
        digest.update(chunk)
        out.write(chunk)
    return digest.hexdigest()


def _write_hashed(planned: PlannedFile, dest: Path) -> str:
    # "xb": never overwrite a file that appeared in the meantime
    with dest.open("xb") as out:
        return _copy_into(planned, out)


# How library files reach the game: cloned on Linux (copy-on-write, e.g. btrfs or XFS: no
# extra space, and the copy stays independent), hard-linked on Windows (NTFS, same drive).
# Whatever cannot be linked is copied.
REFLINK = "reflink"
HARDLINK = "hard"
_LINK: str | None = (
    HARDLINK if os.name == "nt" else REFLINK if sys.platform.startswith("linux") else None
)
_unlinkable: set[tuple[int, int]] = set()  # (source device, target device) where linking failed


def _source_sha256(source: Path) -> str:
    """The SHA-256 of a linked library file: read once, unlike the copy it replaces."""
    stat = source.stat()
    key = (source, stat.st_size, stat.st_mtime_ns)
    if key not in _hashes:
        with source.open("rb") as fh:  # SHA-256 only: sha256_file also computes an MD5
            _hashes[key] = hashlib.file_digest(fh, "sha256").hexdigest()
    return _hashes[key]


def _clone(src: BinaryIO, out: BinaryIO) -> None:
    import fcntl  # Linux only, like REFLINK

    fcntl.ioctl(out.fileno(), fcntl.FICLONE, src.fileno())


def _place(planned: PlannedFile, dest: Path) -> tuple[str, str | None]:
    """Write a planned file into the game; returns its SHA-256 and how it was linked.

    Generated files and reduced textures are always written; a library file is cloned or
    hard-linked where the filesystem allows it, else copied (None). Like a copy, neither
    replaces a file that appeared in the meantime.
    """
    if _LINK is None or planned.content is not None or planned.reduction is not None:
        return _write_hashed(planned, dest), None
    devices = (planned.source.stat().st_dev, dest.parent.stat().st_dev)
    if devices in _unlinkable:
        return _write_hashed(planned, dest), None
    if _LINK == HARDLINK:
        try:
            os.link(planned.source, dest)
        except FileExistsError:
            raise
        except OSError:  # another drive, a filesystem without hard links
            _unlinkable.add(devices)
            return _write_hashed(planned, dest), None
        return _source_sha256(planned.source), HARDLINK
    with planned.source.open("rb") as src, dest.open("xb") as out:
        try:
            _clone(src, out)
        except OSError:  # no copy-on-write here, or another filesystem
            _unlinkable.add(devices)
            return _copy_into(planned, out), None
    return _source_sha256(planned.source), REFLINK


def _library_changed(item: dict, path: Path) -> bool:
    """Whether a changed file of ours is still hard-linked: its library copy changed too."""
    if item.get("link") != HARDLINK or not item.get("linked"):
        return False
    try:
        return os.path.samefile(path, item["linked"])
    except OSError:
        return False


def _note_changed(result: ApplyResult | UnapplyResult, item: dict, path: Path) -> None:
    result.kept.append(item["path"])
    if _library_changed(item, path):
        result.library_changed.setdefault(item.get("mod", ""), []).append(item["path"])


def _output_sha256(planned: PlannedFile) -> str:
    """The SHA-256 the planned file would be written with, without writing it."""
    digest = hashlib.sha256()
    for chunk in _chunks(planned):
        digest.update(chunk)
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


def _on_disk(item: dict, path: Path) -> str:
    """Whether a recorded file is still as we wrote it: "same", "missing" or "modified"."""
    if path.is_symlink():
        return "modified"
    try:
        stat = path.stat()
    except OSError:
        return "missing"
    if (stat.st_size, stat.st_mtime_ns) == (item.get("size"), item.get("mtime_ns")):
        return "same"
    if item.get("sha256") and sha256_file(path) == item["sha256"]:
        return "same"  # touched, or recorded before sizes were: the content is ours
    return "modified"


def _stat(path: Path) -> dict:
    stat = path.stat()
    return {"size": stat.st_size, "mtime_ns": stat.st_mtime_ns}


def _undo(record: dict) -> None:
    """Remove everything an unfinished apply was setting up, leaving nothing applied.

    Files marked `new` were absent when that run started; the others are only removed while
    they are still as we wrote them, so nothing foreign or changed by hand is touched.
    """
    user_dir = Path(record["user_dir"])
    for item in record.get("files", []):
        path = user_dir / normalize_rel(item["path"])
        if item.get("new"):
            if path.is_file() or path.is_symlink():
                path.unlink()
        elif _on_disk(item, path) == "same":
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


def _reduced(planned: PlannedFile) -> list[int] | None:
    """The size a texture is written at, if it is reduced."""
    reduction = planned.reduction
    return None if reduction is None else [reduction.width, reduction.height]


def _unchanged(item: dict, planned: PlannedFile) -> bool:
    """Whether the recorded file is what the plan wants now (it is on disk as written).

    A record from before signatures existed is checked by content once, and the signature
    added, so that the next apply is quick again.
    """
    if "source" not in item:
        try:
            if _output_sha256(planned) != item.get("sha256"):
                return False
        except OSError:
            return False
        item.update(source=planned.signature(), reduced=_reduced(planned))
        return True
    if item.get("source") != planned.signature():
        return False
    filled = planned.content is not None  # generated at apply: compare the result
    if filled and hashlib.sha256(planned.content).hexdigest() != item.get("sha256"):
        return False
    return item.get("reduced") == _reduced(planned)


def apply(
    plan: Plan,
    user_dir: Path,
    deployed_file: Path,
    progress: Progress | None = None,
) -> ApplyResult:
    """Bring the game to `plan`, touching only what differs from the last apply.

    Files applied before and still wanted unchanged are left alone; files no longer wanted,
    or wanted differently, are removed (unless changed by hand since); new ones are written.
    All or nothing: the intent is written down first. If anything fails, everything of ours
    is removed and nothing is applied; if the process dies, `rollback` does the same on the
    next start. A file dao-modman did not write is never overwritten.
    """
    if not user_dir.is_dir():
        raise DeployError(f"game user dir not found: {user_dir}")
    rollback(deployed_file)
    previous = load_record(deployed_file) or {}
    if previous and Path(previous["user_dir"]) != user_dir:
        unapply(deployed_file, progress)  # another game dir: start from nothing
        previous = {}
    old = {item["path"].casefold(): item for item in previous.get("files", [])}

    result = ApplyResult()
    resolver = _Resolver(user_dir)
    kept: dict[Path, dict] = {}  # ours, still wanted as it is
    removals: dict[Path, dict] = {}  # ours, to go
    writes: dict[Path, PlannedFile] = {}
    for index, planned in enumerate(plan.files):
        dest = resolver.resolve(planned.target)
        key = dest.relative_to(user_dir).as_posix().casefold()
        item = old.pop(key, None)
        if dest in writes or dest in kept:
            result.skipped.append(planned.target)
        elif item is not None:
            state = _on_disk(item, dest)
            if state == "modified":
                _note_changed(result, item, dest)
            elif state == "same" and _unchanged(item, planned):
                if "size" not in item:  # recorded before sizes were
                    item.update(_stat(dest))
                kept[dest] = item
            else:
                if state == "same":
                    removals[dest] = item
                writes[dest] = planned
        elif dest.exists() or dest.is_symlink():
            result.skipped.append(planned.target)
        else:
            writes[dest] = planned
        if progress:
            progress("Comparing with the last apply", index + 1, len(plan.files))
    for item in old.values():  # no longer wanted
        dest = user_dir / normalize_rel(item["path"])
        state = _on_disk(item, dest)
        if state == "same":
            removals[dest] = item
        elif state == "modified":
            _note_changed(result, item, dest)

    addins_xml = resolve_case(user_dir, addins.ADDINS_REL)
    wanted = {addin.uid.casefold() for addin in plan.addins}
    ours = [uid for uid in previous.get("addins", []) if uid.casefold() in wanted]
    gone = [uid for uid in previous.get("addins", []) if uid.casefold() not in wanted]
    try:
        new_addins = addins.absent(addins_xml, plan.addins)
    except (ET.ParseError, OSError, ValueError) as exc:
        raise DeployError(f"cannot read {addins_xml}: {exc}") from exc
    ours += [uid for uid in new_addins if uid.casefold() not in {u.casefold() for u in ours}]

    rel = {dest: dest.relative_to(user_dir).as_posix() for dest in [*kept, *writes]}
    new_files = {dest: {"path": rel[dest], "mod": planned.mod_id, "new": True}
                 for dest, planned in writes.items()}  # fmt: skip
    created = _missing_dirs(user_dir, [*writes, addins_xml] if new_addins else list(writes))
    record: dict = {
        "state": APPLYING,
        "user_dir": str(user_dir),
        "files": [*kept.values(), *removals.values(), *new_files.values()],
        "dirs": [*previous.get("dirs", []), *created],
        "addins": ours,
    }
    _save_record(deployed_file, record)
    try:
        for dest in removals:
            dest.unlink()
        result.removed = len(removals)
        if gone:
            result.unregistered = addins.unregister(addins_xml, gone)
        for index, (dest, planned) in enumerate(writes.items()):
            dest.parent.mkdir(parents=True, exist_ok=True)
            entry = new_files[dest]
            entry["sha256"], how = _place(planned, dest)
            if how:
                result.linked += 1
            if how == HARDLINK:  # changes to it change the library copy: see _library_changed
                entry.update(link=HARDLINK, linked=str(planned.source))
            entry.update(_stat(dest), source=planned.signature())
            entry["reduced"] = _reduced(planned)
            del entry["new"]
            if progress:
                progress("Writing files", index + 1, len(writes))
        if new_addins:
            addins_xml.parent.mkdir(parents=True, exist_ok=True)
            addins.register(addins_xml, plan.addins)
    except BaseException as exc:
        _undo(record)
        deployed_file.unlink(missing_ok=True)
        if isinstance(exc, Exception):
            raise DeployError(f"apply failed and was rolled back: {exc}") from exc
        raise
    # folders we created that the removals emptied
    dirs = record["dirs"]
    for folder in sorted(dirs, key=lambda d: d.count("/"), reverse=True):
        with contextlib.suppress(OSError):  # not empty
            (user_dir / normalize_rel(folder)).rmdir()
    record["dirs"] = [folder for folder in dirs if (user_dir / normalize_rel(folder)).is_dir()]
    record["files"] = [*kept.values(), *new_files.values()]
    record["state"] = APPLIED
    _save_record(deployed_file, record)
    result.written, result.unchanged, result.addins = len(writes), len(kept), new_addins
    return result


# what applying would change for a mod
NOT_APPLIED = "not applied"
CHANGED = "changed"
APPLIED = "applied"
STILL_APPLIED = "still applied"  # disabled or removed, its files still in the game


@dataclass
class Comparison:
    mods: dict[str, str] = field(default_factory=dict)  # mod id -> one of the states above
    write: int = 0
    remove: int = 0
    unchanged: int = 0

    @property
    def pending(self) -> list[str]:
        return [mod_id for mod_id, state in self.mods.items() if state != APPLIED]


def compare(
    plan: Plan, record: dict | None, foreign: list[tuple[str, Path]] | None = None
) -> Comparison:
    """What the next apply would change, mod by mod, from the plan and the last record.

    Only signatures are compared, nothing on disk is read: files changed by hand show up when
    applying. Files a foreign file stands in the way of are left out, as apply skips them;
    generated item tables count as applied while their mod is.
    """
    recorded: dict[str, dict[str, object]] = {}
    for item in (record or {}).get("files", []):
        recorded.setdefault(item.get("mod", ""), {})[item["path"].casefold()] = item.get("source")
    blocked = {rel.casefold() for rel, _path in foreign or []}
    planned: dict[str, dict[str, str]] = {}
    for planned_file in plan.files:
        target = planned_file.target.casefold()
        if target not in blocked:
            planned.setdefault(planned_file.mod_id, {})[target] = planned_file.signature()
    result = Comparison()
    for mod_id in planned.keys() | recorded.keys():
        want, have = planned.get(mod_id, {}), recorded.get(mod_id, {})
        # a file recorded before signatures existed counts as applied; apply checks it
        same = sum(
            target in have and have[target] in (signature, None)
            for target, signature in want.items()
        )
        result.unchanged += same
        result.write += len(want) - same
        result.remove += sum(target not in want for target in have)
        if mod_id == MERGED:
            continue  # merged files belong to all their mods: not a mod's state
        if not want:
            result.mods[mod_id] = STILL_APPLIED
        elif not have:
            result.mods[mod_id] = NOT_APPLIED
        else:
            result.mods[mod_id] = APPLIED if same == len(want) == len(have) else CHANGED
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
                _note_changed(result, item, path)
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
