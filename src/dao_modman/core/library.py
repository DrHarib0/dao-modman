"""The mod library: `mods/<id>/{mod.toml,files/}` plus the profile (order, enabled, groups)."""

from __future__ import annotations

import contextlib
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path

from . import archive as archives
from . import dazip, metadata, overridepkg, pathops, revisions
from .dazip import AddIn
from .ignore import DEFAULT_PATTERNS, IgnoreRules
from .models import (
    ArchiveInfo,
    Mod,
    NexusInfo,
    Profile,
    ProfileEntry,
)
from .overridepkg import OptionKey
from .paths import Dirs

FILES_DIR = "files"


class LibraryError(Exception):
    pass


@dataclass
class PackageOptions:
    """The options of one override package inside a mod."""

    root: str  # the package's folder inside the mod's files ("" = top level)
    folder: str  # target folder of its OverrideConfig.xml, relative to the game user dir
    keys: list[OptionKey]


def slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")
    return slug or "mod"


class Library:
    def __init__(
        self, dirs: Dirs, ignore: Iterable[str] | None = None, *, load: bool = True
    ) -> None:
        """With `load=False` the library starts empty, until `reload` is called."""
        self.dirs = dirs
        self.ignore = IgnoreRules(DEFAULT_PATTERNS if ignore is None else ignore)
        dirs.ensure()
        self.mods: dict[str, Mod] = {}
        self.errors: list[str] = []
        self.profile = Profile()
        self._listings: dict[str, list[str]] = {}  # mod id -> its files, see `list_files`
        if load:
            self.reload()

    # -- loading / saving -------------------------------------------------

    def reload(self, progress: Callable[[str, int, int], None] | None = None) -> None:
        """Read every mod and the profile; `progress` is told about each mod."""
        self.mods, self.errors, self._listings = {}, [], {}
        mod_dirs = [
            mod_dir
            for mod_dir in sorted(self.dirs.mods.iterdir())
            if mod_dir.is_dir() and not mod_dir.name.startswith(".")
        ]
        for index, mod_dir in enumerate(mod_dirs):
            if progress:
                progress("Loading the library", index + 1, len(mod_dirs))
            try:
                mod = metadata.load_mod(mod_dir)
            except metadata.MetadataError as exc:
                self.errors.append(str(exc))
                continue
            if mod.id != mod_dir.name:
                self.errors.append(f"{mod_dir}: id {mod.id!r} does not match directory name")
                continue
            # the kind depends on the ignore rules, so it is worked out afresh
            self.mods[mod.id] = mod
            mod.kind = pathops.kind(self.list_files(mod.id), self.ignore.matches)
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

    def save_profile(self, action: str = "change the profile") -> None:
        """Write profile.toml; `action` describes the change for the revision history."""
        metadata.save_profile(self.dirs.profile_file, self.profile)
        revisions.snapshot(self.dirs.data, action)

    def save_mod(self, mod: Mod, action: str | None = None) -> None:
        """Write the mod's mod.toml; `action` describes the change for the revision history."""
        metadata.save_mod(self.dirs.mods / mod.id, mod)
        revisions.snapshot(self.dirs.data, action or f"change {mod.id}")

    # -- queries ----------------------------------------------------------

    def files_dir(self, mod_id: str) -> Path:
        return self.dirs.mods / mod_id / FILES_DIR

    def list_files(self, mod_id: str) -> list[str]:
        """The files of a mod (see `archive.list_files`); not to be changed by the caller.

        A mod's files only change when the library imports, replaces or removes it, so the
        listing is kept until then (files changed there by hand show after a restart).
        """
        if mod_id not in self._listings:
            self._listings[mod_id] = archives.list_files(self.files_dir(mod_id))
        return self._listings[mod_id]

    def ordered(self) -> list[tuple[ProfileEntry, Mod]]:
        return [(entry, self.mods[entry.id]) for entry in self.profile.mods]

    def options(self, mod: Mod, problems: list[str] | None = None) -> list[PackageOptions]:
        """The option sets of the mod's override packages (one per OverrideConfig.xml)."""
        files_dir = self.files_dir(mod.id)
        files = self.list_files(mod.id)
        if not any(rel.rpartition("/")[2].casefold() == overridepkg.CONFIG for rel in files):
            return []
        _dazips, packages = pathops.package_roots(files)
        found = []
        for rel in files:
            root = dazip.owner(packages, rel)
            if root is None or rel.rpartition("/")[2].casefold() != overridepkg.CONFIG:
                continue
            payload = rel[len(root) + 1 if root else 0 :].split("/")[2:-1]
            try:
                keys = overridepkg.parse_config(files_dir / rel)
            except overridepkg.OverridePkgError as exc:
                if problems is not None:
                    problems.append(f"{mod.id}: {exc}")
                continue
            found.append(PackageOptions(root, "/".join(payload), keys))
        return found

    def default_targets(
        self, mod: Mod, problems: list[str] | None = None, *, ignored: bool = False
    ) -> dict[str, str]:
        """Target path -> source file, before the mod's path operations.

        Options of override packages are resolved here, so path operations see one file per
        option and never the unused variants. With `ignored`, ignored files are included at
        the place they would go: for showing them, never for deploying.
        """
        files = self.list_files(mod.id)
        mapping = pathops.default_mapping(mod, files, None if ignored else self.ignore.matches)
        for package in self.options(mod, problems):
            mapping, warnings = overridepkg.apply_options(
                mapping, package.keys, mod.options, package.root, package.folder
            )
            if problems is not None:
                problems += [f"{mod.id}: {warning}" for warning in warnings]
        return mapping

    def selection(
        self, mod: Mod, problems: list[str] | None = None
    ) -> tuple[dict[str, str], dict[str, str]]:
        """`(installed, left out)` target -> source mappings: the ignore patterns and the
        mod's exclude/include operations applied."""
        mapping = self.default_targets(mod, problems, ignored=True)
        return pathops.select(mapping, self.ignore.matches, mod.ops)

    def targets(self, mod: Mod, problems: list[str] | None = None) -> dict[str, str]:
        """Target path -> source file (relative to the mod's files dir) of what is installed."""
        return self.selection(mod, problems)[0]

    def set_option(self, mod: Mod, key: OptionKey, label: str) -> None:
        """Choose a value for an option; the package default is not stored."""
        mod.options = {k: v for k, v in mod.options.items() if k.casefold() != key.id.casefold()}
        value = key.value(label)
        if value is not None and value is not key.value(key.default):
            mod.options[key.id] = value.label
        self.save_mod(mod, f"{mod.id}: set option {key.id} = {label}")

    def addins(self, mod: Mod, targets: dict[str, str] | None = None) -> list[AddIn]:
        """The mod's AddIns to register: those whose dazip still has files to deploy."""
        files_dir = self.files_dir(mod.id)
        roots = dazip.roots(self.list_files(mod.id))
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
        """Extract into `stage/files`; packages inside become folders of the same name."""
        files = stage / FILES_DIR
        archives.extract(archive, files)
        for nested in archives.list_files(files):
            if nested.lower().endswith((".dazip", ".override")):
                unpacked = stage / "unpacked"
                archives.extract(files / nested, unpacked)
                (files / nested).unlink()
                unpacked.rename(files / nested)
        return files

    def find_nexus(self, mod_id: int, file_id: int) -> Mod | None:
        """The mod installed from this Nexus file, if any."""
        return next(
            (
                mod
                for mod in self.mods.values()
                if mod.nexus and (mod.nexus.mod_id, mod.nexus.file_id) == (mod_id, file_id)
            ),
            None,
        )

    def find_archive(self, archive: Path) -> Mod | None:
        """The mod imported from an archive with the same content, if any."""
        archive = archive.expanduser()
        if not archive.is_file():
            return None  # importing it reports the problem
        sha256 = archives.sha256_file(archive)
        return next(
            (mod for mod in self.mods.values() if mod.archive and mod.archive.sha256 == sha256),
            None,
        )

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
        mod.kind = pathops.kind(listing, self.ignore.matches)
        mod.name, mod.version = name or mod.name, version
        mod.addin_uids = [addin.uid for addin in addins]
        if len(addins) == 1:  # a mod that is one AddIn is best described by it
            mod.name = name or addins[0].name
            mod.version = version or addins[0].version
        packages = pathops.package_roots(listing)[1]
        if not addins and len(packages) == 1:  # likewise for a single override package
            with contextlib.suppress(overridepkg.OverridePkgError):
                package = overridepkg.parse_manifest(files / next(iter(packages.values())))
                mod.name = name or package.name or mod.name
                mod.version = version or package.version

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
        self._listings.pop(mod.id, None)
        self.profile.mods.append(ProfileEntry(id=mod.id))
        self.save_profile(f"import {mod.id} from {archive.name}")
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
            self._listings.pop(mod.id, None)
            self.save_mod(mod, f"{mod.id}: replace archive with {archive.name}")
        except (archives.ArchiveError, dazip.DazipError) as exc:
            raise LibraryError(str(exc)) from exc
        finally:
            shutil.rmtree(stage, ignore_errors=True)
        return mod

    def remove(self, mod_id: str, *, delete_archive: bool = False) -> None:
        mod = self.mods.pop(mod_id)
        self._listings.pop(mod_id, None)
        shutil.rmtree(self.dirs.mods / mod_id)
        self.profile.mods = [entry for entry in self.profile.mods if entry.id != mod_id]
        self.save_profile(f"remove {mod_id}")
        if delete_archive and mod.archive:
            in_use = any(
                m.archive and m.archive.file == mod.archive.file for m in self.mods.values()
            )
            if not in_use:
                (self.dirs.downloads / Path(mod.archive.file).name).unlink(missing_ok=True)
