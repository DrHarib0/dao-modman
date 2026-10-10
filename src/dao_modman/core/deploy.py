"""Deployment: plan (ops, priority, conflicts), apply to the game user dir, unapply."""

from __future__ import annotations

import contextlib
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path

from . import addins, dazip
from .archive import sha256_file
from .dazip import AddIn
from .library import Library
from .metadata import write_atomic
from .models import KIND_DAZIP
from .paths import normalize_rel, resolve_case

_CHUNK = 1024 * 1024


class DeployError(Exception):
    pass


@dataclass
class PlannedFile:
    target: str
    source: Path
    mod_id: str


@dataclass
class Plan:
    files: list[PlannedFile] = field(default_factory=list)
    # target -> mod ids providing it, in load order (the last one wins)
    conflicts: dict[str, list[str]] = field(default_factory=dict)
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


def build_plan(library: Library) -> Plan:
    plan = Plan()
    winners: dict[str, PlannedFile] = {}
    providers: dict[str, list[str]] = {}
    for entry, mod in library.ordered():
        if not entry.enabled:
            continue
        files_dir = library.files_dir(mod.id)
        for target, source in library.targets(mod).items():
            key = target.casefold()
            winners[key] = PlannedFile(normalize_rel(target), files_dir / source, mod.id)
            providers.setdefault(key, []).append(mod.id)
        if mod.kind == KIND_DAZIP and (manifest := dazip.find_manifest(files_dir)):
            plan.addins += dazip.parse_manifest(manifest)
    plan.files = sorted(winners.values(), key=lambda planned: planned.target.casefold())
    plan.conflicts = {
        winners[key].target: mod_ids
        for key, mod_ids in sorted(providers.items())
        if len(mod_ids) > 1
    }
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


def _copy_hashed(source: Path, dest: Path) -> str:
    digest = hashlib.sha256()
    # "xb": never overwrite a file that appeared in the meantime
    with source.open("rb") as src, dest.open("xb") as out:
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
            digest = _copy_hashed(planned.source, dest)
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
