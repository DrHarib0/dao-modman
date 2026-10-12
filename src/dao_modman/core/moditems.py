"""The items a mod adds to the game, by the names they have in game."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from . import erf, gff, itemlists
from .deploy import Progress
from .library import Library
from .models import Mod

ITEM_SUFFIX = ".uti"
BASE_ITEM_TABLE = "bitm_base"  # the 2DA tables that name the kinds of items
Types = dict[int, tuple[str, int | None]]  # base item id -> (label, name string id)


@dataclass(frozen=True)
class ModItem:
    name: str  # as shown in game; the file name if no name is known
    file: str
    named: bool = True
    type: str = ""  # what kind of item, e.g. "Longsword"; empty if unknown


@dataclass
class ModItems:
    added: list[ModItem] = field(default_factory=list)  # sorted by name
    replaced: int = 0  # item files that replace one of the game's own
    problems: list[str] = field(default_factory=list)


@dataclass
class GameData:
    """What reading the game's files gave; kept for the session, as reading takes a while."""

    tables: dict[Path, tuple[tuple[int, int], dict[int, str]]] = field(default_factory=dict)
    items: dict[Path, set[str]] = field(default_factory=dict)
    types: dict[Path, Types] = field(default_factory=dict)

    def item_types(self, install_dir: Path | None, progress: Progress | None) -> Types:
        """The kinds of items the game defines."""
        if install_dir is None or not install_dir.is_dir():
            return {}
        if install_dir not in self.types:
            archives = sorted(p for p in install_dir.rglob("*") if p.suffix.casefold() == ".erf")
            found: Types = {}
            for index, path in enumerate(archives):
                found.update(_types_in(path))
                if progress:
                    progress("Reading item types", index + 1, len(archives))
            self.types[install_dir] = found
        return self.types[install_dir]

    def game_items(self, install_dir: Path | None, progress: Progress | None) -> set[str]:
        """Case-folded file names of the game's own items."""
        if install_dir is None or not install_dir.is_dir():
            return set()
        if install_dir not in self.items:
            names = itemlists.game_items(install_dir, progress)
            self.items[install_dir] = {name.casefold() for name in names}
        return self.items[install_dir]

    def strings(
        self, tables: list[Path], problems: list[str], progress: Progress | None
    ) -> dict[int, str]:
        """String id -> text; later tables override earlier ones."""
        merged: dict[int, str] = {}
        for index, path in enumerate(tables):
            try:
                stat = path.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
                if self.tables.get(path, (None, {}))[0] != signature:
                    self.tables[path] = (signature, gff.read_tlk(path))
                merged.update(self.tables[path][1])
            except (OSError, gff.GffError) as exc:
                problems.append(str(exc))
            if progress:
                progress("Reading names", index + 1, len(tables))
        return merged


def _is_type_table(name: str) -> bool:
    folded = name.casefold()
    return folded.startswith(BASE_ITEM_TABLE) and folded.endswith(".gda")


def _read_types(data: bytes) -> Types:
    found: Types = {}
    for row in gff.read_gda(data, ["ID", "Label", "NameStrRef"]):
        if isinstance(row.get("ID"), int):
            strref = row.get("NameStrRef")
            label = row.get("Label")
            found[row["ID"]] = (
                label if isinstance(label, str) else "",
                strref if isinstance(strref, int) and strref else None,
            )
    return found


def _types_in(path: Path) -> Types:
    """Item types from a base item table: a loose one, or those inside an archive."""
    found: Types = {}
    try:
        if path.suffix.casefold() == ".gda":
            return _read_types(path.read_bytes()) if _is_type_table(path.name) else {}
        archive = erf.open_archive(path)
        for entry in archive.entries:
            if archive.readable and _is_type_table(entry.name):
                found.update(_read_types(erf.read(archive, entry)))
    except (OSError, erf.ErfError, gff.GffError):
        pass
    return found


def _type_name(types: Types, base: object, strings: dict[int, str]) -> str:
    label, strref = types.get(base, ("", None)) if isinstance(base, int) else ("", None)
    named = strings.get(strref, "").strip() if strref is not None else ""
    return named or label.partition(" - ")[2].strip() or label.strip()


def _mod_files(library: Library, mod: Mod, suffixes: tuple[str, ...]) -> list[Path]:
    files_dir = library.files_dir(mod.id)
    return [
        files_dir / source
        for target, source in library.targets(mod).items()
        if target.casefold().endswith(suffixes)
    ]


def talk_tables(install_dir: Path | None, library: Library, mod: Mod, language: str) -> list[Path]:
    """Talk tables of one language, lowest priority first.

    The game's, those of the enabled mods in load order, then the mod's own: its names win,
    and they are found even while it is switched off.
    """
    suffix = f"_{language}.tlk".casefold()
    tables: list[Path] = []
    if install_dir is not None and install_dir.is_dir():

        def rank(path: Path) -> tuple[bool, str]:
            top = path.relative_to(install_dir).parts[0].casefold()
            return top in ("addins", "offers"), path.as_posix().casefold()

        found = (p for p in install_dir.rglob("*.tlk") if p.name.casefold().endswith(suffix))
        tables += sorted(found, key=rank)
    for entry, other in library.ordered():
        if entry.enabled and other.id != mod.id:
            tables += _mod_files(library, other, (suffix,))
    return tables + _mod_files(library, mod, (suffix,))


def _item_files(library: Library, mod: Mod, problems: list[str]) -> dict[str, bytes]:
    """The mod's item files, loose or inside its archives: case-folded file name -> data."""
    found: dict[str, tuple[str, bytes]] = {}
    for path in _mod_files(library, mod, (ITEM_SUFFIX, ".erf")):
        try:
            if path.suffix.casefold() == ITEM_SUFFIX:
                found[path.name.casefold()] = (path.name, path.read_bytes())
                continue
            archive = erf.open_archive(path)
            for entry in archive.entries:
                if entry.name.casefold().endswith(ITEM_SUFFIX) and archive.readable:
                    found[entry.name.casefold()] = (entry.name, erf.read(archive, entry))
        except (OSError, erf.ErfError) as exc:
            if path.suffix.casefold() == ITEM_SUFFIX:  # an .erf of another kind is no problem
                problems.append(str(exc))
    return dict(found.values())


def collect(
    library: Library,
    mod: Mod,
    install_dir: Path | None,
    game: GameData,
    language: str = "en-us",
    progress: Progress | None = None,
) -> ModItems:
    """The items `mod` adds, named as in game. Items replacing a game item are only counted."""
    result = ModItems()
    files = _item_files(library, mod, result.problems)
    game_items = game.game_items(install_dir, progress) if files else set()
    new = {file: data for file, data in files.items() if file.casefold() not in game_items}
    result.replaced = len(files) - len(new)
    if not new:
        return result
    tables = talk_tables(install_dir, library, mod, language)
    strings = game.strings(tables, result.problems, progress)
    types = dict(game.item_types(install_dir, progress))
    for path in _mod_files(library, mod, (".gda", ".erf")):  # a mod can define kinds of its own
        types.update(_types_in(path))
    for file, data in new.items():
        name, kind = "", ""
        try:
            fields = gff.read_gff3(data)
            kind = _type_name(types, fields.get("BaseItem"), strings)
            loc = fields.get("LocalizedName")
            if isinstance(loc, gff.LocString):
                name = strings.get(loc.strref, "") if loc.strref is not None else ""
                name = name.strip() or next((t.strip() for t in loc.texts if t.strip()), "")
        except gff.GffError as exc:
            result.problems.append(f"{file}: {exc}")
        result.added.append(ModItem(name or file, file, bool(name), kind))
    result.added.sort(key=lambda item: (item.name.casefold(), item.file.casefold()))
    return result
