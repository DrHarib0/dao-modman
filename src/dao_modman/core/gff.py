"""Minimal readers for the game's GFF files: item templates, talk tables and 2DA tables.

Item templates (`.uti`) are GFF V3.2, the format inherited from the Aurora engine. Talk
tables (`.tlk`) are GFF V4.0 holding a list of (string id, text).
"""

from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass
from pathlib import Path


class GffError(Exception):
    pass


@dataclass(frozen=True)
class LocString:
    """A localized string: an id into the talk tables, and/or text embedded in the file."""

    strref: int | None
    texts: tuple[str, ...] = ()


_NONE = 0xFFFFFFFF  # a null reference in GFF V4.0 data

# GFF V3.2 field types
_DWORD, _FLOAT, _STRING, _RESREF, _LOCSTRING = 4, 8, 10, 11, 12
_INLINE = {0: "<B", 1: "<b", 2: "<H", 3: "<h", 4: "<I", 5: "<i"}


def _require(data: bytes, offset: int, count: int, size: int) -> None:
    """Refuse a table that cannot fit in the data.

    Counts come straight from the file. Without this check a corrupt count would be looped
    over as it stands: slicing past the end never fails, it just yields nothing, forever.
    """
    if size <= 0 or offset + count * size > len(data):
        raise GffError(f"a table of {count} entries does not fit in {len(data)} bytes")


def _wide_string(data: bytes, at: int) -> str:
    """A string stored as a character count followed by UTF-16 text."""
    (length,) = struct.unpack_from("<I", data, at)
    _require(data, at + 4, length, 2)
    return data[at + 4 : at + 4 + length * 2].decode("utf-16-le", "replace").rstrip("\0")


def read_gff3(data: bytes) -> dict[str, object]:
    """The top-level fields of a GFF V3.2 file. Types an item does not need are left out."""
    try:
        if data[4:8] != b"V3.2":
            raise GffError(f"not a GFF V3.2 file: {data[:8]!r}")
        (structs, _nstructs, fields, _nfields, labels, nlabels, field_data, _n, indices) = (
            struct.unpack_from("<9I", data, 8)
        )
        _require(data, labels, nlabels, 16)
        names = [
            data[labels + i * 16 : labels + i * 16 + 16].split(b"\0")[0].decode("latin-1")
            for i in range(nlabels)
        ]
        _type, offset, count = struct.unpack_from("<3I", data, structs)
        if count == 1:
            field_ids = [offset]
        else:
            field_ids = struct.unpack_from(f"<{count}I", data, indices + offset)
        result: dict[str, object] = {}
        for field_id in field_ids:
            kind, label, value = struct.unpack_from("<3I", data, fields + field_id * 12)
            at = field_data + value
            if kind in _INLINE:
                fmt = _INLINE[kind]
                raw = struct.pack("<I", value)[: struct.calcsize(fmt)]
                (result[names[label]],) = struct.unpack(fmt, raw)
            elif kind == _FLOAT:
                (result[names[label]],) = struct.unpack("<f", struct.pack("<I", value))
            elif kind == _STRING:
                (size,) = struct.unpack_from("<I", data, at)
                result[names[label]] = data[at + 4 : at + 4 + size].decode("latin-1")
            elif kind == _RESREF:
                result[names[label]] = data[at + 1 : at + 1 + data[at]].decode("latin-1")
            elif kind == _LOCSTRING:
                _total, strref, parts = struct.unpack_from("<IiI", data, at)
                texts, at = [], at + 12
                for _ in range(parts):
                    _language, size = struct.unpack_from("<iI", data, at)
                    texts.append(data[at + 8 : at + 8 + size].decode("utf-8", "replace"))
                    at += 8 + size
                result[names[label]] = LocString(strref if strref >= 0 else None, tuple(texts))
        return result
    except (struct.error, IndexError) as exc:
        raise GffError(f"malformed GFF V3.2 data: {exc}") from exc


def read_tlk(path: Path) -> dict[int, str]:
    """String id -> text of a talk table (GFF V4.0, type "TLK ")."""
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise GffError(f"cannot read {path}: {exc}") from exc
    try:
        if data[:8] != b"GFF V4.0" or data[12:16] != b"TLK ":
            raise GffError(f"{path}: not a talk table")
        (base,) = struct.unpack_from("<I", data, 24)  # where the data block starts
        (list_at,) = struct.unpack_from("<I", data, base)
        if list_at == _NONE:
            return {}  # a table without strings
        (count,) = struct.unpack_from("<I", data, base + list_at)
        strings: dict[int, str] = {}
        for index in range(count):
            string_id, offset = struct.unpack_from("<2I", data, base + list_at + 4 + index * 8)
            if offset == _NONE:
                continue
            strings[string_id] = _wide_string(data, base + offset)
        return strings
    except (struct.error, IndexError) as exc:
        raise GffError(f"{path}: malformed talk table: {exc}") from exc


def column_id(name: str) -> int:
    """How a 2DA table (`.gda`) identifies a column: the CRC32 of its lower-case UTF-16 name."""
    return zlib.crc32(name.lower().encode("utf-16-le"))


def read_gda(data: bytes, columns: list[str]) -> list[dict[str, object]]:
    """The named columns of every row of a 2DA table (GFF V4.0, type "G2DA").

    Integer columns come back as int, string columns as str; a column the table does not
    have is left out of the rows.
    """
    try:
        if data[:8] != b"GFF V4.0" or data[12:16] != b"G2DA":
            raise GffError(f"not a 2DA table: {data[:16]!r}")
        struct_count, base = struct.unpack_from("<2I", data, 20)
        layouts = {}
        for index in range(struct_count):
            kind, field_count, fields_at, size = struct.unpack_from("<4s3I", data, 28 + index * 16)
            fields = [
                struct.unpack_from("<IHHI", data, fields_at + f * 12) for f in range(field_count)
            ]
            layouts[kind] = (fields, size)
        column_size, (row_fields, row_size) = layouts[b"colm"][1], layouts[b"rows"]
        columns_at, rows_at = struct.unpack_from("<2I", data, base)
        (column_count,) = struct.unpack_from("<I", data, base + columns_at)
        hashes = [
            struct.unpack_from("<I", data, base + columns_at + 4 + c * column_size)[0]
            for c in range(column_count)
        ]
        wanted = {column_id(name): name for name in columns}
        picked = [
            (wanted[column_hash], row_fields[index][1], row_fields[index][3])
            for index, column_hash in enumerate(hashes)
            if column_hash in wanted and index < len(row_fields)
        ]
        (row_count,) = struct.unpack_from("<I", data, base + rows_at)
        _require(data, base + rows_at + 4, row_count, row_size)
        rows = []
        for r in range(row_count):
            at = base + rows_at + 4 + r * row_size
            row: dict[str, object] = {}
            for name, kind, offset in picked:
                (value,) = struct.unpack_from("<I", data, at + offset)
                if kind != 0xE:  # not a string reference
                    row[name] = value
                elif value == _NONE:
                    row[name] = ""
                else:
                    row[name] = _wide_string(data, base + value)
            rows.append(row)
        return rows
    except (struct.error, IndexError, KeyError) as exc:
        raise GffError(f"malformed 2DA table: {exc}") from exc
