"""Writers for minimal game files, so tests need no real game data."""

from __future__ import annotations

import struct
from pathlib import Path

from dao_modman.core import gff


def make_uti(
    resref: str,
    *,
    strref: int | None = None,
    text: str | None = None,
    base_item: int = 5,
    cost: int = 10,
    plot: int = 0,
) -> bytes:
    """An item template (GFF V3.2) with the fields the item scan reads."""
    labels = ["TemplateResRef", "LocalizedName", "BaseItem", "Cost", "Plot"]
    field_data = bytearray()

    def complex_field(blob: bytes) -> int:
        offset = len(field_data)
        field_data.extend(blob)
        return offset

    texts = [text.encode("utf-8")] if text is not None else []
    parts = b"".join(struct.pack("<iI", 0, len(t)) + t for t in texts)
    loc = struct.pack("<iI", -1 if strref is None else strref, len(texts)) + parts
    fields = [
        (11, 0, complex_field(bytes([len(resref)]) + resref.encode("latin-1"))),
        (12, 1, complex_field(struct.pack("<I", len(loc)) + loc)),
        (5, 2, base_item),
        (4, 3, cost),
        (0, 4, plot),
    ]
    structs = struct.pack("<3I", 0xFFFFFFFF, 0, len(fields))
    field_table = b"".join(struct.pack("<3I", *field) for field in fields)
    label_table = b"".join(label.encode().ljust(16, b"\0") for label in labels)
    indices = struct.pack(f"<{len(fields)}I", *range(len(fields)))
    offsets, at = [], 56
    for block, count in (
        (structs, 1),
        (field_table, len(fields)),
        (label_table, len(labels)),
        (bytes(field_data), len(field_data)),
        (indices, len(indices)),
        (b"", 0),
    ):
        offsets += [at, count]
        at += len(block)
    return (
        b"UTI V3.2"
        + struct.pack("<12I", *offsets)
        + structs
        + field_table
        + label_table
        + bytes(field_data)
        + indices
    )


def make_tlk(path: Path, strings: dict[int, str | None]) -> Path:
    """A talk table (GFF V4.0, "TLK " V0.2). A None value is an id without text."""
    path.parent.mkdir(parents=True, exist_ok=True)
    base = 96
    header = b"GFF V4.0PC  TLK V0.2" + struct.pack("<2I", 2, base)
    header = header.ljust(base, b"\0")  # struct definitions are not read by our reader
    entries_at = 4
    table_size = 4 + 8 * len(strings)
    texts, table = b"", b""
    for string_id, text in strings.items():
        if text is None:
            table += struct.pack("<2I", string_id, 0xFFFFFFFF)
            continue
        table += struct.pack("<2I", string_id, entries_at + table_size + len(texts))
        encoded = (text + "\0").encode("utf-16-le")
        texts += struct.pack("<I", len(text) + 1) + encoded
    data = struct.pack("<I", entries_at) + struct.pack("<I", len(strings)) + table + texts
    path.write_bytes(header + data)
    return path


def make_gda(rows: list[tuple[int, str, int]]) -> bytes:
    """A base item table with the columns ID, Label and NameStrRef."""
    columns = ["ID", "Label", "NameStrRef"]
    header_size = 28 + 3 * 16
    field_defs = b"".join(
        struct.pack("<IHHI", *field)
        for field in [
            (10002, 1, 0xC000, 0), (10003, 2, 0xC000, 4),  # gtop: column list, row list
            (10001, 4, 0, 0), (10999, 0, 0, 4),  # colm: hash, type
            (10005, 5, 0, 0), (10006, 0xE, 0, 4), (10007, 5, 0, 8),  # rows
        ]
    )  # fmt: skip
    fields_at = header_size
    base = fields_at + len(field_defs)
    structs = (
        struct.pack("<4s3I", b"gtop", 2, fields_at, 8)
        + struct.pack("<4s3I", b"colm", 2, fields_at + 24, 8)
        + struct.pack("<4s3I", b"rows", 3, fields_at + 48, 12)
    )
    column_list = struct.pack("<I", 3) + b"".join(
        struct.pack("<IB3x", gff.column_id(name), 1) for name in columns
    )
    columns_at, rows_at = 8, 8 + len(column_list)
    strings_at = rows_at + 4 + 12 * len(rows)
    row_data, strings = b"", b""
    for item_id, label, strref in rows:
        row_data += struct.pack("<3I", item_id, strings_at + len(strings), strref)
        strings += struct.pack("<I", len(label) + 1) + (label + "\0").encode("utf-16-le")
    data = struct.pack("<2I", columns_at, rows_at) + column_list
    data += struct.pack("<I", len(rows)) + row_data + strings
    return b"GFF V4.0PC  G2DAV0.2" + struct.pack("<2I", 3, base) + structs + field_defs + data
