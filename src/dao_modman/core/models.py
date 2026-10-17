"""Plain data classes shared by the core and the TUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

KIND_DAZIP = "dazip"
KIND_OVERRIDE = "override"
KIND_PACKAGE = "package"  # override package(s) in DAO-Modmanager's .override format
KIND_MIXED = "mixed"  # more than one of: dazips, override packages, loose override files

OP_EXCLUDE = "exclude"  # do not install
OP_INCLUDE = "include"  # install, although an ignore pattern would leave it out


@dataclass(frozen=True)
class PathOp:
    """The user's decision about some of a mod's files: leave them out, or install them.

    `path` is a target path (relative to the game user dir): a file, a folder or a glob.
    """

    op: str
    path: str

    def describe(self) -> str:
        return f"{self.op} {self.path}"


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
    # choices for an override package's options: "Section/Key" -> value label
    options: dict[str, str] = field(default_factory=dict)


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
