"""Item tables for the "Add Any Item Visual Deluxe" mod (Nexus 1825).

The mod's `get` and `getx` console scripts read two tables listing item files: one for the
game's own items and one for those of mods. The mod ships a Windows scanner to write them;
the same tables are generated here at apply, so they always match what is deployed.
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

from . import deploy, erf
from .paths import resolve_case

GAME_TABLE = "itlst_biow.gda"
MOD_TABLE = "itlst_user.gda"
ITEM_SUFFIX = ".uti"
GAME_DIRS = ("addins", "modules", "packages")  # what the scanner reads, in its order
EXTRA_DIR = "offers"  # not read by the scanner
EXTRA_DIR = "offers"  # not read by the scanner
_OVERRIDE = "packages/core/override/"
_FIRST_ROW = "INVALID"
_HEADER = b"GFF V4.0PC  G2DAV0.2"
# (label, type, flags, offset) of each struct's fields, and the struct's size
_STRUCTS = (
    (b"gtop", ((10002, 1, 0xC000, 0), (10003, 2, 0xC000, 4)), 8),
    (b"colm", ((10001, 4, 0, 0), (10999, 0, 0, 4)), 5),
    (b"rows", ((10005, 5, 0, 0), (10006, 14, 0, 4)), 8),
)
_COLUMNS = (("ID", 1), ("Resource", 0))  # name, column type


def _column_id(name: str) -> int:
    return zlib.crc32(name.lower().encode("utf-16-le"))


def write_table(names: list[str]) -> bytes:
    """A table of item file names, byte for byte as the mod's scanner writes it."""
    fields_at = 28 + 16 * len(_STRUCTS)
    structs, fields = b"", b""
    for kind, struct_fields, size in _STRUCTS:
        structs += struct.pack("<4s3I", kind, len(struct_fields), fields_at + len(fields), size)
        fields += b"".join(struct.pack("<IHHI", *item) for item in struct_fields)
    columns = struct.pack("<I", len(_COLUMNS))
    columns += b"".join(struct.pack("<IB", _column_id(name), kind) for name, kind in _COLUMNS)
    values = [_FIRST_ROW, *names]
    rows_at = 8 + len(columns)
    strings_at = rows_at + 4 + 8 * len(values)
    rows, strings = b"", b""
    for index, value in enumerate(values):
        text = value + "\0"
        rows += struct.pack("<2I", index, strings_at + len(strings))
        strings += struct.pack("<I", len(text)) + text.encode("utf-16-le")
    data = struct.pack("<2I", 8, rows_at) + columns + struct.pack("<I", len(values)) + rows
    head = _HEADER + struct.pack("<2I", len(_STRUCTS), fields_at + len(fields))
    return head + structs + fields + data + strings


def read_table(data: bytes) -> list[str]:
    """The item file names of a table written by `write_table` or by the scanner."""
    try:
        if data[: len(_HEADER)] != _HEADER:
            raise ValueError("not an item table")
        (base,) = struct.unpack_from("<I", data, 24)
        (rows_at,) = struct.unpack_from("<I", data, base + 4)
        (count,) = struct.unpack_from("<I", data, base + rows_at)
        if base + rows_at + 4 + 8 * count > len(data):  # the count comes from the file
            raise ValueError("truncated item table")
        names = []
        for row in range(count):
            (at,) = struct.unpack_from("<I", data, base + rows_at + 8 + 8 * row)
            (length,) = struct.unpack_from("<I", data, base + at)
            text = data[base + at + 4 : base + at + 4 + 2 * length]
            names.append(text.decode("utf-16-le", "replace").rstrip("\0"))
    except struct.error as exc:
        raise ValueError("truncated item table") from exc
    return names[1:]


def _is_item(name: str) -> bool:
    return name.casefold().endswith(ITEM_SUFFIX)


def _items_in(archive: Path) -> list[str] | None:
    """Item names in an archive; None if it cannot be read."""
    try:
        return [entry.name for entry in erf.open_archive(archive).entries if _is_item(entry.name)]
    except erf.ErfError:
        return None


def game_items(install_dir: Path, progress: deploy.Progress | None = None) -> list[str]:
    """The game's item files: the scanner's list, then what the scanner misses.

    The first part is selected and ordered like the scanner does, an item that several
    archives contain being listed once for each. The scanner cannot open the encrypted DLC
    archives and does not look at the offers; their items follow, each name once.
    """
    archives = []
    for name in (*GAME_DIRS, EXTRA_DIR):
        top = resolve_case(install_dir, name)
        if top.is_dir():
            found = [path for path in top.rglob("*") if path.suffix.casefold() == ".erf"]
            found.sort(key=lambda path: path.as_posix().casefold())
            archives += [(path, name == EXTRA_DIR) for path in found]
    names, extra = [], []
    for index, (path, skipped) in enumerate(archives):
        try:
            archive = erf.open_archive(path)
            items = [entry.name for entry in archive.entries if _is_item(entry.name)]
            if skipped or not archive.readable:
                extra += items
            else:
                names += items
        except erf.ErfError:
            pass
        if progress:
            progress("Reading game items", index + 1, len(archives))
    listed = {name.casefold() for name in names}
    for name in extra:
        if name.casefold() not in listed:
            listed.add(name.casefold())
            names.append(name)
    return names


def mod_items(plan: deploy.Plan, unmanaged: list[tuple[str, Path]]) -> list[str]:
    """Item files the mods bring: loose or inside archives, planned or installed by hand."""
    found = []
    sources = [(item.target, item.source) for item in plan.files if item.content is None]
    for target, source in [*sources, *unmanaged]:
        if _is_item(target):
            found.append(target.rpartition("/")[2])
        elif target.casefold().endswith(".erf"):
            found += _items_in(source) or []
    unique: dict[str, str] = {}
    for name in found:
        unique.setdefault(name.casefold(), name)
    return [unique[key] for key in sorted(unique)]


def fill(
    plan: deploy.Plan,
    install_dir: Path | None,
    user_dir: Path,
    deployed_file: Path,
    progress: deploy.Progress | None = None,
) -> str | None:
    """Give the plan's item tables their generated content. Returns a note for the user.

    Does nothing unless the plan deploys the tables, i.e. the mod is enabled.
    """
    tables = {
        item.target.rpartition("/")[2].casefold(): item
        for item in plan.files
        if item.target.casefold().startswith(_OVERRIDE)
    }
    game_table, mod_table = tables.get(GAME_TABLE), tables.get(MOD_TABLE)
    if game_table is None and mod_table is None:
        return None
    notes = []
    if game_table is not None:
        if install_dir is None or not install_dir.is_dir():
            notes.append(f"{GAME_TABLE} left as shipped: game install dir not found")
        else:
            names = game_items(install_dir, progress)
            game_table.content = write_table(names)
            notes.append(f"{len(names)} game items")
    if mod_table is not None:
        names = mod_items(plan, deploy.unmanaged(user_dir, deployed_file, install_dir))
        mod_table.content = write_table(names)
        notes.append(f"{len(names)} mod items")
    return "Item tables: " + ", ".join(notes)
