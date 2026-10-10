"""Large Address Aware patch: swap the game executable for the bundled LAA-patched copy.

A 32-bit Windows process only gets 2 GB of address space unless its executable carries the
LAA flag; heavily modded games run out of that quickly.
"""

from __future__ import annotations

import os
import shutil
import struct
from importlib import resources
from pathlib import Path

from .paths import resolve_case

EXE_REL = "bin_ship/DAOrigins.exe"
BACKUP_SUFFIX = ".pre-laa"
BUNDLED = "assets/laa/DAOrigins.exe"

PATCHED = "patched"
UNPATCHED = "unpatched"

_LARGE_ADDRESS_AWARE = 0x0020


class LaaError(Exception):
    pass


def is_laa(exe: Path) -> bool:
    """True if the PE executable has the Large Address Aware flag set."""
    try:
        with exe.open("rb") as fh:
            header = fh.read(0x40)
            if len(header) < 0x40 or header[:2] != b"MZ":
                raise LaaError(f"{exe} is not a Windows executable")
            fh.seek(struct.unpack_from("<I", header, 0x3C)[0])
            coff = fh.read(24)
    except OSError as exc:
        raise LaaError(f"cannot read {exe}: {exc}") from exc
    if len(coff) < 24 or coff[:4] != b"PE\0\0":
        raise LaaError(f"{exe} is not a Windows executable")
    return bool(struct.unpack_from("<H", coff, 22)[0] & _LARGE_ADDRESS_AWARE)


def bundled_exe() -> Path | None:
    """The LAA-patched executable shipped with dao-modman, if present."""
    path = resolve_case(Path(str(resources.files("dao_modman"))), BUNDLED)
    return path if path.is_file() else None


def game_exe(install_dir: Path) -> Path:
    exe = resolve_case(install_dir, EXE_REL)
    if not exe.is_file():
        raise LaaError(f"game executable not found: {exe}")
    return exe


def backup_path(exe: Path) -> Path:
    return exe.with_name(exe.name + BACKUP_SUFFIX)


def status(install_dir: Path) -> str:
    return PATCHED if is_laa(game_exe(install_dir)) else UNPATCHED


def _replace(source: Path, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        shutil.copyfile(source, tmp)
        tmp.chmod(0o755)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def patch(install_dir: Path, patched_exe: Path | None = None) -> Path:
    """Install the LAA executable. Returns the path of the backup of the original."""
    exe = game_exe(install_dir)
    if is_laa(exe):
        raise LaaError("the game executable is already Large Address Aware")
    source = patched_exe or bundled_exe()
    if source is None:
        raise LaaError(
            f"no LAA executable bundled: add it as src/dao_modman/{BUNDLED} or pass a path"
        )
    if not is_laa(source):
        raise LaaError(f"{source} is not Large Address Aware")
    backup = backup_path(exe)
    try:
        if not backup.exists():
            shutil.copy2(exe, backup)
        _replace(source, exe)
    except OSError as exc:
        raise LaaError(f"cannot patch {exe}: {exc}") from exc
    return backup


def restore(install_dir: Path) -> Path:
    """Put the original executable back. Returns its path."""
    exe = resolve_case(install_dir, EXE_REL)
    backup = backup_path(exe)
    if not backup.is_file():
        raise LaaError(f"no backup found at {backup} (Steam's 'verify files' also restores it)")
    try:
        os.replace(backup, exe)
    except OSError as exc:
        raise LaaError(f"cannot restore {exe}: {exc}") from exc
    return exe
