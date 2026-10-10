"""TOML persistence for `mod.toml` and `profile.toml`."""

from __future__ import annotations

import os
import tomllib
from pathlib import Path
from typing import Any

import tomli_w

from . import pathops
from .models import (
    OP_REMOVE,
    OP_RENAME,
    ArchiveInfo,
    Mod,
    NexusInfo,
    PathOp,
    Profile,
    ProfileEntry,
)
from .paths import normalize_rel

MOD_FILE = "mod.toml"


class MetadataError(Exception):
    pass


def write_atomic(path: Path, data: bytes) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def op_to_dict(op: PathOp) -> dict[str, str]:
    if op.op == OP_RENAME:
        return {"op": OP_RENAME, "from": op.path, "to": op.to or ""}
    return {"op": OP_REMOVE, "path": op.path}


def op_from_dict(raw: dict[str, Any]) -> PathOp:
    """Parse and validate an op; mod.toml may have been edited by hand."""
    try:
        kind = raw["op"]
        if kind == OP_RENAME:
            return pathops.validate(PathOp(OP_RENAME, str(raw["from"]), str(raw["to"])))
        return pathops.validate(PathOp(str(kind), str(raw["path"])))
    except (KeyError, pathops.PathOpError) as exc:
        raise MetadataError(f"invalid path operation {raw!r}: {exc}") from exc


def mod_to_dict(mod: Mod) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": mod.id,
        "name": mod.name,
        "version": mod.version,
        "kind": mod.kind,
        "added": mod.added,
    }
    if mod.archive:
        data["archive"] = {
            "file": mod.archive.file,
            "sha256": mod.archive.sha256,
            "md5": mod.archive.md5,
        }
    if mod.nexus:
        nexus: dict[str, Any] = {
            "game": mod.nexus.game,
            "mod_id": mod.nexus.mod_id,
            "file_id": mod.nexus.file_id,
            "file_name": mod.nexus.file_name,
        }
        if mod.nexus.uploaded:
            nexus["uploaded"] = mod.nexus.uploaded
        data["nexus"] = nexus
    if mod.addin_uids:
        data["addin"] = {"uids": mod.addin_uids}
    if mod.ops:
        data["ops"] = [op_to_dict(op) for op in mod.ops]
    return data


def mod_from_dict(raw: dict[str, Any]) -> Mod:
    try:
        mod = Mod(
            id=normalize_rel(str(raw["id"])),
            name=str(raw["name"]),
            version=str(raw.get("version", "")),
            kind=str(raw.get("kind", "override")),
        )
        if "added" in raw:
            mod.added = raw["added"]
        if archive := raw.get("archive"):
            mod.archive = ArchiveInfo(
                file=str(archive["file"]),
                sha256=str(archive.get("sha256", "")),
                md5=str(archive.get("md5", "")),
            )
        if nexus := raw.get("nexus"):
            mod.nexus = NexusInfo(
                game=str(nexus["game"]),
                mod_id=int(nexus["mod_id"]),
                file_id=int(nexus["file_id"]),
                file_name=str(nexus.get("file_name", "")),
                uploaded=nexus.get("uploaded"),
            )
        mod.addin_uids = [str(uid) for uid in raw.get("addin", {}).get("uids", [])]
    except (KeyError, ValueError, TypeError) as exc:
        raise MetadataError(f"invalid mod metadata: {exc}") from exc
    if "/" in mod.id:
        raise MetadataError(f"invalid mod id {mod.id!r}")
    mod.ops = [op_from_dict(op) for op in raw.get("ops", [])]
    return mod


def load_mod(mod_dir: Path) -> Mod:
    path = mod_dir / MOD_FILE
    try:
        with path.open("rb") as fh:
            return mod_from_dict(tomllib.load(fh))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise MetadataError(f"{path}: {exc}") from exc
    except MetadataError as exc:
        raise MetadataError(f"{path}: {exc}") from exc


def save_mod(mod_dir: Path, mod: Mod) -> None:
    write_atomic(mod_dir / MOD_FILE, tomli_w.dumps(mod_to_dict(mod)).encode())


def load_profile(path: Path) -> Profile:
    if not path.is_file():
        return Profile()
    try:
        with path.open("rb") as fh:
            raw = tomllib.load(fh)
        return Profile(
            mods=[
                ProfileEntry(
                    id=str(entry["id"]),
                    enabled=bool(entry.get("enabled", True)),
                    group=str(entry.get("group", "")),
                )
                for entry in raw.get("mods", [])
            ],
            hide_official=bool(raw.get("hide_official", False)),
        )
    except (OSError, tomllib.TOMLDecodeError, KeyError, TypeError) as exc:
        raise MetadataError(f"{path}: {exc}") from exc


def save_profile(path: Path, profile: Profile) -> None:
    data = {
        "hide_official": profile.hide_official,
        "mods": [
            {"id": entry.id, "enabled": entry.enabled, "group": entry.group}
            for entry in profile.mods
        ],
    }
    write_atomic(path, tomli_w.dumps(data).encode())
