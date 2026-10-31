"""Large Address Aware patch: swap the game executable for an LAA copy of one the user has.

A 32-bit Windows process only gets 2 GB of address space unless its executable carries the
LAA flag; heavily modded games run out of that quickly. A DRM-free executable is flagged on
the way in; Steam's carries DRM (a `.bind` section) that breaks when the header changes.
"""

from __future__ import annotations

import os
import shutil
import struct
from pathlib import Path

from .paths import resolve_case

EXE_REL = "bin_ship/DAOrigins.exe"
BACKUP_SUFFIX = ".pre-laa"
# how to get a DRM-free executable; the Steam one cannot simply be flagged in place
GUIDE_URL = "https://steamcommunity.com/sharedfiles/filedetails/?id=233222451"

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


def _pe_layout(data: bytes, name: str) -> tuple[int, int, list[str]]:
    """Offsets of the COFF header and the optional header, and the section names."""
    try:
        if data[:2] != b"MZ":
            raise ValueError
        coff = struct.unpack_from("<I", data, 0x3C)[0]
        if data[coff : coff + 4] != b"PE\0\0":
            raise ValueError
        count = struct.unpack_from("<H", data, coff + 6)[0]
        optional_size = struct.unpack_from("<H", data, coff + 20)[0]
        optional = coff + 24
        if optional_size < 68 or len(data) < optional + optional_size + 40 * count:
            raise ValueError
    except (ValueError, struct.error):
        raise LaaError(f"{name} is not a Windows executable") from None
    table = optional + optional_size
    sections = [
        data[table + 40 * i : table + 40 * i + 8].rstrip(b"\0").decode("latin-1")
        for i in range(count)
    ]
    return coff, optional, sections


def has_steam_drm(data: bytes, name: str = "the executable") -> bool:
    """Steam's DRM wraps the executable and adds a `.bind` section."""
    return ".bind" in _pe_layout(data, name)[2]


def pe_checksum(data: bytes, field: int) -> int:
    """The PE header checksum; the checksum field itself, at offset `field`, counts as 0."""
    padded = data[:field] + bytes(4) + data[field + 4 :]
    if len(padded) % 2:
        padded += b"\0"
    total = sum(struct.unpack(f"<{len(padded) // 2}H", padded))
    while total > 0xFFFF:
        total = (total & 0xFFFF) + (total >> 16)
    return total + len(data)


def flagged(data: bytes, name: str = "the executable") -> bytes:
    """`data` with the LAA flag set and the header checksum updated."""
    coff, optional, _ = _pe_layout(data, name)
    out = bytearray(data)
    characteristics = struct.unpack_from("<H", out, coff + 22)[0]
    struct.pack_into("<H", out, coff + 22, characteristics | _LARGE_ADDRESS_AWARE)
    checksum = optional + 64
    struct.pack_into("<I", out, checksum, pe_checksum(bytes(out), checksum))
    return bytes(out)


def game_exe(install_dir: Path) -> Path:
    exe = resolve_case(install_dir, EXE_REL)
    if not exe.is_file():
        raise LaaError(f"game executable not found: {exe}")
    return exe


def backup_path(exe: Path) -> Path:
    return exe.with_name(exe.name + BACKUP_SUFFIX)


def status(install_dir: Path) -> str:
    return PATCHED if is_laa(game_exe(install_dir)) else UNPATCHED


def _replace(data: bytes, dest: Path) -> None:
    tmp = dest.with_name(dest.name + ".tmp")
    try:
        tmp.write_bytes(data)
        tmp.chmod(0o755)
        os.replace(tmp, dest)
    finally:
        tmp.unlink(missing_ok=True)


def patch(install_dir: Path, source_exe: Path) -> tuple[Path, bool]:
    """Install `source_exe`, LAA-flagged if it is not yet (which needs it to be DRM-free).

    Returns the path of the backup of the original, and whether the flag was set here.
    """
    exe = game_exe(install_dir)
    if is_laa(exe):
        raise LaaError("the game executable is already Large Address Aware")
    source = source_exe.expanduser()
    if not source.is_file():
        raise LaaError(f"{source} not found")
    was_flagged = not is_laa(source)
    try:
        data = source.read_bytes()
    except OSError as exc:
        raise LaaError(f"cannot read {source}: {exc}") from exc
    if was_flagged:
        if has_steam_drm(data, str(source)):
            raise LaaError(
                f"{source} carries Steam's DRM and cannot be flagged; "
                f"a DRM-free executable is needed: {GUIDE_URL}"
            )
        data = flagged(data, str(source))
    backup = backup_path(exe)
    try:
        if not backup.exists():
            shutil.copy2(exe, backup)
        _replace(data, exe)
    except OSError as exc:
        raise LaaError(f"cannot patch {exe}: {exc}") from exc
    return backup, was_flagged


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
