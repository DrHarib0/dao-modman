"""Plain data classes shared by the core and the TUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatchcase

KIND_DAZIP = "dazip"
KIND_OVERRIDE = "override"
KIND_MIXED = "mixed"  # dazip(s) plus loose override files

OP_REMOVE = "remove"
OP_RENAME = "rename"


@dataclass(frozen=True)
class PathOp:
    """One user edit of a mod's target paths (relative to the game user dir)."""

    op: str
    path: str
    to: str | None = None

    def describe(self) -> str:
        if self.op == OP_RENAME:
            return f"rename {self.path} -> {self.to}"
        return f"remove {self.path}"


@dataclass
class Overwrite:
    """Declares that overwriting files of another mod is intended."""

    mod: str  # id of the mod whose files may be overwritten
    files: list[str] = field(default_factory=list)  # names or globs; empty: every file

    def covers(self, mod_id: str, name: str) -> bool:
        if mod_id != self.mod:
            return False
        if not self.files:
            return True
        candidates = {name.casefold(), name.rpartition("/")[2].casefold()}
        return any(
            pattern.casefold() in candidates
            or any(fnmatchcase(candidate, pattern.casefold()) for candidate in candidates)
            for pattern in self.files
        )


@dataclass
class ArchiveInfo:
    file: str
    sha256: str
    md5: str


@dataclass
class NexusInfo:
    game: str
    mod_id: int
    file_id: int
    file_name: str = ""
    uploaded: datetime | None = None


@dataclass
class Mod:
    id: str
    name: str
    version: str = ""
    kind: str = KIND_OVERRIDE
    added: datetime = field(default_factory=lambda: datetime.now(UTC).replace(microsecond=0))
    archive: ArchiveInfo | None = None
    nexus: NexusInfo | None = None
    addin_uids: list[str] = field(default_factory=list)
    ops: list[PathOp] = field(default_factory=list)
    overwrites: list[Overwrite] = field(default_factory=list)

    def may_overwrite(self, mod_id: str, name: str) -> bool:
        return any(overwrite.covers(mod_id, name) for overwrite in self.overwrites)


@dataclass
class ProfileEntry:
    id: str
    enabled: bool = True
    group: str = ""


@dataclass
class Profile:
    """Load order: later entries have higher priority."""

    mods: list[ProfileEntry] = field(default_factory=list)
    hide_official: bool = False  # hide the game's own DLC in the mod list

    def entry(self, mod_id: str) -> ProfileEntry | None:
        return next((entry for entry in self.mods if entry.id == mod_id), None)

    def move(self, mod_id: str, delta: int) -> bool:
        index = next((i for i, entry in enumerate(self.mods) if entry.id == mod_id), None)
        if index is None:
            return False
        target = index + delta
        if not 0 <= target < len(self.mods):
            return False
        self.mods.insert(target, self.mods.pop(index))
        return True
