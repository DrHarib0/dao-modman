"""Resolve a mod's target paths: default mapping plus the user's remove/rename operations."""

from __future__ import annotations

from fnmatch import fnmatchcase
from typing import TypeVar

from . import dazip
from .models import KIND_DAZIP, KIND_MIXED, KIND_OVERRIDE, OP_REMOVE, OP_RENAME, Mod, PathOp
from .paths import normalize_rel

T = TypeVar("T")

OVERRIDE_DIR = "packages/core/override"
_GLOB_CHARS = frozenset("*?[")


class PathOpError(Exception):
    pass


DOCUMENT_SUFFIXES = (".txt", ".pdf")


def is_documentation(target: str) -> bool:
    """Readmes and other documents that mods ship next to their game files."""
    name = target.rsplit("/", 1)[-1].casefold()
    return name.endswith(DOCUMENT_SUFFIXES) or "readme" in name


def default_mapping(mod: Mod, files: list[str]) -> dict[str, str]:
    """Map target path (relative to the game user dir) -> file inside `mods/<id>/files/`.

    Files of a dazip go where its `Contents/` folder says; whatever else is in the dazip
    (Manifest.xml, readmes) is not installed. Files outside any dazip are loose override
    files.
    """
    roots = dazip.roots(files)
    mapping: dict[str, str] = {}
    for rel in files:
        root = dazip.owner(roots, rel)
        if root is None:
            mapping[f"{OVERRIDE_DIR}/{mod.id}/{rel}"] = rel
            continue
        head, _, rest = rel[len(root) + 1 if root else 0 :].partition("/")
        if head.casefold() == dazip.CONTENTS and rest:
            mapping[rest] = rel
    return mapping


def kind(files: list[str]) -> str:
    """What a mod consists of: dazip(s), loose override files, or both.

    Documents do not count as loose files: a dazip shipped with a readme is still a dazip.
    """
    roots = dazip.roots(files)
    if not roots:
        return KIND_OVERRIDE
    loose = (rel for rel in files if dazip.owner(roots, rel) is None)
    return KIND_MIXED if any(not is_documentation(rel) for rel in loose) else KIND_DAZIP


def _is_glob(pattern: str) -> bool:
    return any(char in _GLOB_CHARS for char in pattern)


def _prefixes(path: str) -> list[str]:
    parts = path.split("/")
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


def matches(pattern: str, path: str, *, glob: bool = True) -> bool:
    """True if `pattern` names `path` or one of its parent directories (case-insensitive)."""
    pattern, path = pattern.casefold(), path.casefold()
    wanted = pattern.split("/")
    if path.split("/")[: len(wanted)] == wanted:
        return True
    return glob and _is_glob(pattern) and any(fnmatchcase(p, pattern) for p in _prefixes(path))


def validate(op: PathOp) -> PathOp:
    """Return a normalized copy of `op`, raising PathOpError if it is unusable."""
    try:
        if op.op == OP_REMOVE:
            return PathOp(OP_REMOVE, normalize_rel(op.path))
        if op.op == OP_RENAME:
            if op.to is None:
                raise ValueError("rename needs a destination")
            return PathOp(OP_RENAME, normalize_rel(op.path), normalize_rel(op.to))
    except ValueError as exc:
        raise PathOpError(f"{op.describe()}: {exc}") from exc
    raise PathOpError(f"unknown operation {op.op!r}")


def apply_op(mapping: dict[str, T], op: PathOp) -> dict[str, T]:
    op = validate(op)
    result: dict[str, T] = {}
    moved: dict[str, T] = {}
    # Globs only apply to removes, and only when the pattern is not an existing literal path.
    glob = op.op == OP_REMOVE and not any(matches(op.path, t, glob=False) for t in mapping)
    for target, source in mapping.items():
        if not matches(op.path, target, glob=glob):
            result[target] = source
        elif op.op == OP_RENAME:
            rest = target.split("/")[op.path.count("/") + 1 :]
            moved["/".join([op.to, *rest])] = source
    # Like `mv`: a renamed file replaces whatever was at the destination.
    folded = {target.casefold() for target in moved}
    result = {target: src for target, src in result.items() if target.casefold() not in folded}
    result.update(moved)
    return result


def apply_ops(mapping: dict[str, T], ops: list[PathOp]) -> dict[str, T]:
    """Run `ops` in order over a target -> source mapping."""
    for op in ops:
        mapping = apply_op(mapping, op)
    return dict(sorted(mapping.items()))


def resolve(mod: Mod, files: list[str]) -> dict[str, str]:
    """Final target -> source mapping for a mod."""
    return apply_ops(default_mapping(mod, files), mod.ops)
