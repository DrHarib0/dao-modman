"""Plain data classes shared by the core and the TUI."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime

KIND_DAZIP = "dazip"
KIND_OVERRIDE = "override"

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


@dataclass
class ProfileEntry:
    id: str
    enabled: bool = True
    group: str = ""


@dataclass
class Profile:
    """Load order: later entries have higher priority."""

    mods: list[ProfileEntry] = field(default_factory=list)

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
