"""Which game resources exist: in the game's archives and in the enabled mods.

Used to check references, e.g. whether a hairstyle listed for the character creator has a
model behind it. Only names are collected, so encrypted DLC archives are covered too.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import erf
from .library import Library

Progress = Callable[[str, int, int], None]
ARCHIVE_SUFFIXES = (".erf", ".rim")


@dataclass
class Resources:
    names: set[str] = field(default_factory=set)  # file names, case-folded
    stems: set[str] = field(default_factory=set)  # the same without extension
    problems: list[str] = field(default_factory=list)

    def add(self, name: str) -> None:
        folded = name.casefold()
        self.names.add(folded)
        self.stems.add(folded.rpartition(".")[0] or folded)

    def exists(self, name: str) -> bool:
        """True if a resource has this name, given with or without its extension."""
        folded = name.casefold()
        return folded in self.names or folded in self.stems


def _archives(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*") if p.suffix.casefold() in ARCHIVE_SUFFIXES)


def collect(
    install_dir: Path | None, library: Library, progress: Progress | None = None
) -> Resources:
    """Names of everything the game can load: its own archives plus the enabled mods' files."""
    resources = Resources()
    archives = _archives(install_dir) if install_dir and install_dir.is_dir() else []
    for entry, mod in library.ordered():
        if not entry.enabled:
            continue
        files_dir = library.files_dir(mod.id)
        for target, source in library.targets(mod).items():
            resources.add(target.rpartition("/")[2])
            if target.casefold().endswith(ARCHIVE_SUFFIXES):
                archives.append(files_dir / source)
    for index, path in enumerate(archives):
        try:
            for member in erf.open_archive(path).entries:
                resources.add(member.name)
        except erf.ErfError as exc:
            resources.problems.append(str(exc))
        if progress:
            progress("Reading archives", index + 1, len(archives))
    return resources


def read_game_file(install_dir: Path | None, name: str) -> bytes | None:
    """A file from the game's own archives, by name; the last archive that has it wins."""
    if install_dir is None or not install_dir.is_dir():
        return None
    wanted, found = name.casefold(), None
    for path in _archives(install_dir):
        try:
            archive = erf.open_archive(path)
            for member in archive.entries:
                if member.name.casefold() == wanted and archive.readable:
                    found = erf.read(archive, member)
        except erf.ErfError:
            continue
    return found
