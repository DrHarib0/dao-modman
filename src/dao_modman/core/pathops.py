"""Resolve a mod's target paths: the default mapping, then which files are installed.

The ignore patterns leave some files out; the user's exclude and include operations decide
about the rest. Where files go is fixed: the game finds override files by name.
"""

from __future__ import annotations

from collections.abc import Callable
from fnmatch import fnmatchcase

from . import dazip, overridepkg
from .models import (
    KIND_DAZIP,
    KIND_MIXED,
    KIND_OVERRIDE,
    KIND_PACKAGE,
    OP_EXCLUDE,
    OP_INCLUDE,
    Mod,
    PathOp,
)
from .paths import normalize_rel

OVERRIDE_DIR = "packages/core/override"
_GLOB_CHARS = frozenset("*?[")


class PathOpError(Exception):
    pass


# Tells whether a file (by its path inside the mod) is ignored, i.e. never deployed.
Ignored = Callable[[str], bool]


def package_roots(files: list[str]) -> tuple[dict[str, str], dict[str, str]]:
    """`(dazip roots, override package roots)`: folder -> path of its Manifest.xml."""
    dazips = dazip.roots(files)
    packages = {
        folder: manifest
        for folder, manifest in overridepkg.roots(files).items()
        if folder not in dazips
    }
    return dazips, packages


def default_mapping(mod: Mod, files: list[str], ignored: Ignored | None = None) -> dict[str, str]:
    """Map target path (relative to the game user dir) -> file inside `mods/<id>/files/`.

    Files of a dazip go where its `Contents/` folder says, files of an override package
    where its `BioWare/Dragon Age/` folder says; whatever else is in such a package
    (Manifest.xml, extras) is not installed. Files outside any package are loose override
    files. Ignored files are left out altogether.
    """
    dazips, packages = package_roots(files)
    roots = {**dazips, **packages}
    mapping: dict[str, str] = {}
    for rel in files:
        if ignored is not None and ignored(rel):
            continue
        root = dazip.owner(roots, rel)
        if root is None:
            mapping[f"{OVERRIDE_DIR}/{mod.id}/{rel}"] = rel
        elif root in packages:
            if target := overridepkg.target(root, rel):
                mapping[target] = rel
        else:
            head, _, rest = rel[len(root) + 1 if root else 0 :].partition("/")
            if head.casefold() == dazip.CONTENTS and rest:
                mapping[rest] = rel
    return mapping


def kind(files: list[str], ignored: Ignored | None = None) -> str:
    """What a mod consists of: dazip(s), override package(s), loose override files, or a mix.

    Ignored files do not count as loose files: a dazip shipped with a readme is still a dazip.
    """
    dazips, packages = package_roots(files)
    roots = {**dazips, **packages}
    loose = any(dazip.owner(roots, rel) is None and not (ignored and ignored(rel)) for rel in files)
    kinds = [
        kind
        for kind, present in (
            (KIND_DAZIP, bool(dazips)),
            (KIND_PACKAGE, bool(packages)),
            (KIND_OVERRIDE, loose or not roots),
        )
        if present
    ]
    return kinds[0] if len(kinds) == 1 else KIND_MIXED


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
    if op.op not in (OP_EXCLUDE, OP_INCLUDE):
        raise PathOpError(f"unknown operation {op.op!r}")
    try:
        return PathOp(op.op, normalize_rel(op.path))
    except ValueError as exc:
        raise PathOpError(f"{op.describe()}: {exc}") from exc


def select[T](
    mapping: dict[str, T], ignored: Callable[[T], bool] | None, ops: list[PathOp]
) -> tuple[dict[str, T], dict[str, T]]:
    """Split a target -> source mapping into `(installed, left out)`.

    A file starts out installed unless `ignored` (the ignore patterns) says otherwise; then
    each operation, in order, excludes or includes the files it names. A glob only counts as
    one when it is not also the literal path of a file or folder.
    """
    installed = {t: not (ignored and ignored(source)) for t, source in mapping.items()}
    for op in map(validate, ops):
        glob = not any(matches(op.path, target, glob=False) for target in mapping)
        for target in mapping:
            if matches(op.path, target, glob=glob):
                installed[target] = op.op == OP_INCLUDE
    chosen = {t: source for t, source in sorted(mapping.items()) if installed[t]}
    left_out = {t: source for t, source in sorted(mapping.items()) if not installed[t]}
    return chosen, left_out


def resolve(mod: Mod, files: list[str], ignored: Ignored | None = None) -> dict[str, str]:
    """Final target -> source mapping for a mod: what gets installed."""
    return select(default_mapping(mod, files), ignored, mod.ops)[0]
