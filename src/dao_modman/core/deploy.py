"""Deployment: plan (ops, priority, conflicts, merges), apply to the game user dir, unapply."""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import addins, chargen, dazip
from .archive import sha256_file
from .dazip import AddIn
from .library import Library
from .metadata import write_atomic
from .models import KIND_DAZIP
from .paths import normalize_rel, resolve_case

_CHUNK = 1024 * 1024


class DeployError(Exception):
    pass


MERGED = "(merged)"


@dataclass
class PlannedFile:
    target: str
    source: Path
    mod_id: str
    content: bytes | None = None  # generated file: written instead of copying `source`


@dataclass
class Plan:
    files: list[PlannedFile] = field(default_factory=list)
    # winning target -> mod ids providing it, in load order (the last one wins)
    conflicts: dict[str, list[str]] = field(default_factory=dict)
    # generated target -> mod ids whose files were merged into it
    merged: dict[str, list[str]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    addins: list[AddIn] = field(default_factory=list)

    def conflicting_mods(self) -> set[str]:
        return {mod_id for mod_ids in self.conflicts.values() for mod_id in mod_ids}


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


def build_plan(library: Library) -> Plan:
    plan = Plan()
    candidates: dict[tuple[str, ...], list[PlannedFile]] = {}
    chargens: dict[str, list[PlannedFile]] = {}
    for entry, mod in library.ordered():
        if not entry.enabled:
            continue
        files_dir = library.files_dir(mod.id)
        for target, source in library.targets(mod).items():
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
        if mod.kind == KIND_DAZIP and (manifest := dazip.find_manifest(files_dir)):
            plan.addins += dazip.parse_manifest(manifest)

    for items in candidates.values():
        # Only the last mod's files are deployed; duplicates inside one mod are its business.
        winner = items[-1]
        plan.files += [item for item in items if item.mod_id == winner.mod_id]
        if len(mod_ids := _mod_ids(items)) > 1:
            plan.conflicts[winner.target] = mod_ids

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
            plan.conflicts[items[-1].target] = mod_ids
            continue
        scope_dir = _override_scope(items[0].target)[0]
        target = f"{scope_dir}/{chargen.FILE_NAME}"
        plan.files.append(PlannedFile(target, items[-1].source, MERGED, content))
        plan.merged[target] = mod_ids

    plan.files.sort(key=lambda planned: planned.target.casefold())
    plan.conflicts = dict(sorted(plan.conflicts.items()))
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


def _make_parents(user_dir: Path, directory: Path, created: list[str]) -> None:
    missing = []
    current = directory
    while current != user_dir and not current.exists():
        missing.append(current)
        current = current.parent
    directory.mkdir(parents=True, exist_ok=True)
    created += [path.relative_to(user_dir).as_posix() for path in reversed(missing)]


def apply(plan: Plan, user_dir: Path, deployed_file: Path) -> ApplyResult:
    """Replace the current deployment (if any) with `plan`."""
    if not user_dir.is_dir():
        raise DeployError(f"game user dir not found: {user_dir}")
    unapply(deployed_file)

    result = ApplyResult()
    record: dict = {"user_dir": str(user_dir), "files": [], "dirs": [], "addins": []}
    try:
        for planned in plan.files:
            dest = resolve_case(user_dir, planned.target)
            if dest.exists() or dest.is_symlink():
                result.skipped.append(planned.target)
                continue
            _make_parents(user_dir, dest.parent, record["dirs"])
            digest = _write_hashed(planned, dest)
            record["files"].append(
                {
                    "path": dest.relative_to(user_dir).as_posix(),
                    "sha256": digest,
                    "mod": planned.mod_id,
                }
            )
            result.written += 1
        if plan.addins:
            addins_xml = resolve_case(user_dir, addins.ADDINS_REL)
            _make_parents(user_dir, addins_xml.parent, record["dirs"])
            record["addins"] = result.addins = addins.register(addins_xml, plan.addins)
    finally:
        # Also record a partial deployment, so it can be unapplied.
        if record["files"] or record["dirs"] or record["addins"]:
            _save_record(deployed_file, record)
    return result


def unapply(deployed_file: Path) -> UnapplyResult:
    """Remove what the last apply wrote, leaving modified files alone."""
    result = UnapplyResult()
    record = load_record(deployed_file)
    if record is None:
        return result
    user_dir = Path(record["user_dir"])
    for item in record.get("files", []):
        path = user_dir / normalize_rel(item["path"])
        if path.is_symlink() or not path.is_file():
            continue
        if sha256_file(path) == item["sha256"]:
            path.unlink()
            result.removed += 1
        else:
            result.kept.append(item["path"])
    for rel in sorted(record.get("dirs", []), key=lambda d: d.count("/"), reverse=True):
        with contextlib.suppress(OSError):  # not empty or already gone
            (user_dir / normalize_rel(rel)).rmdir()
    if uids := record.get("addins"):
        addins_xml = resolve_case(user_dir, addins.ADDINS_REL)
        result.addins = addins.unregister(addins_xml, uids)
    deployed_file.unlink()
    return result
