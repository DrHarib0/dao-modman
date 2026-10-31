"""Hashing and safe extraction of mod archives (zip/dazip/override, 7z, rar)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import py7zr

from . import windows
from .paths import normalize_rel

_CHUNK = 1024 * 1024
_SEVENZIP_MAGIC = b"7z\xbc\xaf\x27\x1c"
_RAR_MAGIC = b"Rar!\x1a\x07"


class ArchiveError(Exception):
    pass


def file_hashes(path: Path) -> tuple[str, str]:
    """Return (sha256, md5) of a file."""
    sha256, md5 = hashlib.sha256(), hashlib.md5(usedforsecurity=False)
    with path.open("rb") as fh:
        while chunk := fh.read(_CHUNK):
            sha256.update(chunk)
            md5.update(chunk)
    return sha256.hexdigest(), md5.hexdigest()


def sha256_file(path: Path) -> str:
    return file_hashes(path)[0]


def _safe_name(name: str) -> str:
    try:
        if name.startswith(("/", "\\")):
            raise ValueError("absolute path")
        return normalize_rel(name)
    except ValueError as exc:
        raise ArchiveError(f"unsafe path in archive: {name!r}") from exc


def _extract_zip(archive: Path, dest: Path) -> None:
    with zipfile.ZipFile(archive) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            target = dest / _safe_name(info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as out:
                shutil.copyfileobj(src, out, _CHUNK)


def _extract_7z(archive: Path, dest: Path) -> None:
    with py7zr.SevenZipFile(archive) as sz:
        for name in sz.getnames():
            _safe_name(name)
        sz.extractall(dest)


def find_tool(name: str) -> str | None:
    """The program `name` on PATH; on Windows also where 7-Zip or WinRAR is installed.

    Neither installer adds itself to PATH. bsdtar is not looked for: Windows' own tar.exe is
    one, but cannot read solid archives.
    """
    if found := shutil.which(name):
        return found
    if name == "7z":
        key = r"SOFTWARE\7-Zip"
        folders = [windows.registry_value(key, "Path64"), windows.registry_value(key, "Path")]
        folders += [windows.app_folder("7zFM.exe")]
        folders += [folder / "7-Zip" for folder in windows.program_files()]
        path = windows.find_program("7z.exe", folders)
    elif name == "unrar":
        folders = [windows.app_folder("WinRAR.exe")]
        folders += [folder / "WinRAR" for folder in windows.program_files()]
        path = windows.find_program("UnRAR.exe", folders)
    else:
        path = None
    return str(path) if path else None


def _extract_rar(archive: Path, dest: Path, unrar_command: list[str] | None = None) -> None:
    """Extract with whichever tool manages: no pure-Python rar reader exists.

    unrar and 7z come first because bsdtar cannot read solid archives, which are common.
    All three refuse to write outside the target directory by default. `unrar_command`, from
    config.toml, is the last resort: `{archive}` and `{dest}` in it are replaced, and it runs
    inside `dest`.
    """
    commands: dict[str, list[str]] = {}
    if unrar := find_tool("unrar"):
        commands["unrar"] = [unrar, "x", "-y", "-o+", str(archive), f"{dest}{os.sep}"]
    if sevenzip := find_tool("7z"):
        commands["7z"] = [sevenzip, "x", "-y", f"-o{dest}", str(archive)]
    if bsdtar := find_tool("bsdtar"):
        commands["bsdtar"] = [bsdtar, "-xf", str(archive), "-C", str(dest)]
    if unrar_command:
        archive_path, dest_path = str(archive.resolve()), str(dest.resolve())
        commands["unrar_command"] = [
            part.replace("{archive}", archive_path).replace("{dest}", dest_path)
            for part in unrar_command
        ]
    failures = []
    for tool, command in commands.items():
        cwd = dest if tool == "unrar_command" else None
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, stdin=subprocess.DEVNULL, cwd=cwd
            )
        except OSError as exc:  # e.g. the configured program does not exist
            failures.append(f"{tool}: {exc}")
            continue
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout).strip().splitlines()
        failures.append(f"{tool}: {detail[-1] if detail else 'failed'}")
        shutil.rmtree(dest)  # whatever the failed tool left behind must not be imported
        dest.mkdir()
    if not failures:
        raise ArchiveError(
            "rar archives need unrar, 7z or bsdtar (on Windows: WinRAR or 7-Zip), "
            "or unrar_command in config.toml"
        )
    raise ArchiveError(f"cannot extract {archive.name} ({'; '.join(failures)})")


def _drop_links(dest: Path) -> None:
    """Archives may carry symlinks; they must never be followed when deploying."""
    for root, dirnames, filenames in os.walk(dest):
        for name in dirnames + filenames:
            path = Path(root, name)
            if path.is_symlink():
                path.unlink()


def extract(archive: Path, dest: Path, unrar_command: list[str] | None = None) -> None:
    """Extract `archive` into the (new or empty) directory `dest`.

    `unrar_command`: see `_extract_rar`.
    """
    dest.mkdir(parents=True, exist_ok=True)
    with archive.open("rb") as fh:
        magic = fh.read(8)
    try:
        if magic.startswith(_SEVENZIP_MAGIC):
            _extract_7z(archive, dest)
        elif magic.startswith(_RAR_MAGIC):
            _extract_rar(archive, dest, unrar_command)
        elif zipfile.is_zipfile(archive):
            _extract_zip(archive, dest)
        else:
            raise ArchiveError(f"unsupported archive format: {archive.name}")
    except (zipfile.BadZipFile, py7zr.exceptions.ArchiveError, OSError) as exc:
        raise ArchiveError(f"cannot extract {archive.name}: {exc}") from exc
    _drop_links(dest)


def list_files(root: Path) -> list[str]:
    """Relative posix paths of all regular files below `root`, sorted.

    Links are neither listed nor followed. Plain strings and `os.scandir` instead of `Path`
    objects: a large mod has thousands of files, and the listing is needed often.
    """
    files: list[str] = []

    def walk(directory: str, prefix: str) -> None:
        try:
            entries = list(os.scandir(directory))
        except OSError:
            return  # gone or unreadable: like os.walk, nothing to list
        for entry in entries:
            if entry.is_dir(follow_symlinks=False):
                walk(entry.path, f"{prefix}{entry.name}/")
            elif entry.is_file(follow_symlinks=False):
                files.append(prefix + entry.name)

    walk(os.fspath(root), "")
    return sorted(files)
