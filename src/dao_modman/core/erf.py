"""Read the game's ERF archives (V2.0 and V2.2).

Only what an item scan needs: the table of contents, and member data of plain archives. The
DLC archives are V2.2 with their contents encrypted; their member names are still readable.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from pathlib import Path

_MAGIC_SIZE = 16  # "ERF V2.0" as UTF-16
_NAME_SIZE = 64  # 32 UTF-16 characters


class ErfError(Exception):
    pass


@dataclass(frozen=True)
class Entry:
    name: str
    offset: int
    size: int


@dataclass
class Archive:
    path: Path
    version: str
    entries: list[Entry]
    readable: bool  # False: contents are encrypted or compressed, only names are known


def open_archive(path: Path) -> Archive:
    """Read an archive's table of contents."""
    try:
        with path.open("rb") as fh:
            head = fh.read(56)
            version = head[:_MAGIC_SIZE].decode("utf-16-le", "replace")
            if version == "ERF V2.0":
                (count,) = struct.unpack_from("<I", head, 16)
                toc_at, entry_size, readable = 32, 72, True
            elif version == "ERF V2.2":
                count, _year, _day, _unknown, flags = struct.unpack_from("<5I", head, 16)
                toc_at, entry_size = 56, 76
                encrypted, compressed = (flags >> 4) & 0xF, flags >> 29
                readable = not encrypted and not compressed
            else:
                raise ErfError(f"{path}: not an ERF V2.0/V2.2 archive")
            # the count comes from the file: never ask for more than the file can hold
            if toc_at + count * entry_size > os.fstat(fh.fileno()).st_size:
                raise ErfError(f"{path}: truncated table of contents")
            fh.seek(toc_at)
            toc = fh.read(count * entry_size)
    except (OSError, struct.error) as exc:
        raise ErfError(f"cannot read {path}: {exc}") from exc
    if len(toc) != count * entry_size:
        raise ErfError(f"{path}: truncated table of contents")
    entries = []
    for at in range(0, len(toc), entry_size):
        name = toc[at : at + _NAME_SIZE].decode("utf-16-le", "replace").split("\0")[0]
        offset, size = struct.unpack_from("<2I", toc, at + _NAME_SIZE)
        entries.append(Entry(name, offset, size))
    return Archive(path, version, entries, readable)


def read(archive: Archive, entry: Entry) -> bytes:
    if not archive.readable:
        raise ErfError(f"{archive.path}: contents are encrypted or compressed")
    try:
        with archive.path.open("rb") as fh:
            if entry.offset + entry.size > os.fstat(fh.fileno()).st_size:
                raise ErfError(f"{archive.path}: {entry.name} is truncated")
            fh.seek(entry.offset)
            data = fh.read(entry.size)
    except OSError as exc:
        raise ErfError(f"cannot read {archive.path}: {exc}") from exc
    if len(data) != entry.size:
        raise ErfError(f"{archive.path}: {entry.name} is truncated")
    return data
