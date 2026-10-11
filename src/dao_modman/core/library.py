"""The mod library: `mods/<id>/{mod.toml,files/}` plus the profile (order, enabled, groups)."""

from __future__ import annotations

import re
import shutil
import tempfile
from pathlib import Path

from . import archive as archives
from . import dazip, metadata, pathops
from .dazip import AddIn
from .models import (
    ArchiveInfo,
    Mod,
    NexusInfo,
    Profile,
    ProfileEntry,
)
from .paths import Dirs

FILES_DIR = "files"


class LibraryError(Exception):
    pass


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "mod"


class Library:
    def __init__(self, dirs: Dirs) -> None:
        self.dirs = dirs
        dirs.ensure()
        self.mods: dict[str, Mod] = {}
        self.errors: list[str] = []
        self.profile = Profile()
        self.reload()

    # -- loading / saving -------------------------------------------------

    def reload(self) -> None:
        self.mods, self.errors = {}, []
        for mod_dir in sorted(self.dirs.mods.iterdir()):
            if not mod_dir.is_dir() or mod_dir.name.startswith("."):
                continue
            try:
                mod = metadata.load_mod(mod_dir)
            except metadata.MetadataError as exc:
                self.errors.append(str(exc))
                continue
            if mod.id != mod_dir.name:
                self.errors.append(f"{mod_dir}: id {mod.id!r} does not match directory name")
                continue
            self.mods[mod.id] = mod
        try:
            self.profile = metadata.load_profile(self.dirs.profile_file)
        except metadata.MetadataError as exc:
            self.errors.append(str(exc))
            self.profile = Profile()
        self._sync_profile()

    def _sync_profile(self) -> None:
        seen: set[str] = set()
        entries = []
        for entry in self.profile.mods:
            if entry.id in self.mods and entry.id not in seen:
                seen.add(entry.id)
                entries.append(entry)
        entries += [ProfileEntry(id=mod_id) for mod_id in self.mods if mod_id not in seen]
        self.profile.mods = entries

    def save_profile(self) -> None:
        metadata.save_profile(self.dirs.profile_file, self.profile)

    def save_mod(self, mod: Mod) -> None:
        metadata.save_mod(self.dirs.mods / mod.id, mod)

    # -- queries ----------------------------------------------------------

    def files_dir(self, mod_id: str) -> Path:
        return self.dirs.mods / mod_id / FILES_DIR

    def ordered(self) -> list[tuple[ProfileEntry, Mod]]:
        return [(entry, self.mods[entry.id]) for entry in self.profile.mods]

    def targets(self, mod: Mod) -> dict[str, str]:
        """Target path -> source file (relative to the mod's files dir), with ops applied."""
        return pathops.resolve(mod, archives.list_files(self.files_dir(mod.id)))

    def addins(self, mod: Mod, targets: dict[str, str] | None = None) -> list[AddIn]:
        """The mod's AddIns to register: those whose dazip still has files to deploy."""
        files_dir = self.files_dir(mod.id)
        roots = dazip.roots(archives.list_files(files_dir))
        sources = (self.targets(mod) if targets is None else targets).values()
        used = {dazip.owner(roots, source) for source in sources}
        return [
            addin
            for folder, manifest in roots.items()
            if folder in used
            for addin in dazip.parse_manifest(files_dir / manifest)
        ]

    # -- mutation ---------------------------------------------------------

    def _unique_id(self, name: str) -> str:
        base = slugify(name)
        candidate, counter = base, 2
        while candidate in self.mods or (self.dirs.mods / candidate).exists():
            candidate = f"{base}-{counter}"
            counter += 1
        return candidate

    def _stage(self, archive: Path, stage: Path) -> Path:
        """Extract into `stage/files`, unpacking every dazip inside into a folder of its name."""
        files = stage / FILES_DIR
        archives.extract(archive, files)
        for nested in archives.list_files(files):
            if nested.lower().endswith(".dazip"):
                unpacked = stage / "dazip"
                archives.extract(files / nested, unpacked)
                (files / nested).unlink()
                unpacked.rename(files / nested)
        return files

    def _store(self, archive: Path) -> Path:
        """Make sure the archive lives in `downloads/`; returns its path there."""
        archive = archive.expanduser().resolve()
        if not archive.is_file():
            raise LibraryError(f"not a file: {archive}")
        if archive.parent == self.dirs.downloads.resolve():
            return archive
        stored = self.dirs.downloads / archive.name
        if not stored.exists():
            shutil.copy2(archive, stored)
        elif archives.sha256_file(stored) != archives.sha256_file(archive):
            raise LibraryError(f"a different {archive.name} already exists in downloads")
        return stored

    def _describe(
        self, mod: Mod, archive: Path, files: Path, name: str | None, version: str
    ) -> None:
        """Fill in everything about `mod` that is derived from its archive."""
        if not archives.list_files(files):
            raise LibraryError(f"{archive.name} contains no files")
        sha256, md5 = archives.file_hashes(archive)
        mod.archive = ArchiveInfo(file=archive.name, sha256=sha256, md5=md5)
        listing = archives.list_files(files)
        roots = dazip.roots(listing)
        addins = [a for manifest in roots.values() for a in dazip.parse_manifest(files / manifest)]
        mod.kind = pathops.kind(listing)
        mod.name, mod.version = name or mod.name, version
        mod.addin_uids = [addin.uid for addin in addins]
        if len(addins) == 1:  # a mod that is one AddIn is best described by it
            mod.name = name or addins[0].name
            mod.version = version or addins[0].version

    def import_archive(
        self,
        archive: Path,
        *,
        name: str | None = None,
        version: str = "",
        nexus: NexusInfo | None = None,
    ) -> Mod:
        """Add an archive to the library. Archives outside `downloads/` are copied there."""
        archive = self._store(archive)
        stage = Path(tempfile.mkdtemp(prefix=".import-", dir=self.dirs.mods))
        try:
            files = self._stage(archive, stage)
            mod = Mod(id="", name=archive.stem, nexus=nexus)
            self._describe(mod, archive, files, name, version)
            mod.id = self._unique_id(mod.name)
            metadata.save_mod(stage, mod)
            stage.chmod(0o755)
            stage.rename(self.dirs.mods / mod.id)
        except (archives.ArchiveError, dazip.DazipError) as exc:
            raise LibraryError(str(exc)) from exc
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        self.mods[mod.id] = mod
        self.profile.mods.append(ProfileEntry(id=mod.id))
        self.save_profile()
        return mod

    def replace_archive(
        self, mod: Mod, archive: Path, *, version: str = "", nexus: NexusInfo | None = None
    ) -> Mod:
        """Swap a mod's files for a new archive, keeping its id, load order and path ops."""
        archive = self._store(archive)
        stage = Path(tempfile.mkdtemp(prefix=".import-", dir=self.dirs.mods))
        try:
            files = self._stage(archive, stage)
            self._describe(mod, archive, files, mod.name, version)
            mod.nexus = nexus
            current = self.files_dir(mod.id)
            current.rename(stage / "old")
            files.rename(current)
            self.save_mod(mod)
        except (archives.ArchiveError, dazip.DazipError) as exc:
            raise LibraryError(str(exc)) from exc
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return mod

    def remove(self, mod_id: str, *, delete_archive: bool = False) -> None:
        mod = self.mods.pop(mod_id)
        shutil.rmtree(self.dirs.mods / mod_id)
        self.profile.mods = [entry for entry in self.profile.mods if entry.id != mod_id]
        self.save_profile()
        if delete_archive and mod.archive:
            in_use = any(
                m.archive and m.archive.file == mod.archive.file for m in self.mods.values()
            )
            if not in_use:
                (self.dirs.downloads / Path(mod.archive.file).name).unlink(missing_ok=True)
