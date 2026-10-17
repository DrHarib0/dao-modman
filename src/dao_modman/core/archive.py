"""Hashing and safe extraction of mod archives (zip/dazip/override, 7z, rar)."""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import zipfile
from pathlib import Path

import py7zr

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


def _extract_rar(archive: Path, dest: Path) -> None:
    """Extract with whichever tool manages: no pure-Python rar reader exists.

    unrar and 7z come first because bsdtar cannot read solid archives, which are common.
    All three refuse to write outside the target directory by default.
    """
    commands = {
        "unrar": ["unrar", "x", "-y", "-o+", str(archive), f"{dest}{os.sep}"],
        "7z": ["7z", "x", "-y", f"-o{dest}", str(archive)],
        "bsdtar": ["bsdtar", "-xf", str(archive), "-C", str(dest)],
    }
    failures = []
    for tool, command in commands.items():
        if not shutil.which(tool):
            continue
        result = subprocess.run(command, capture_output=True, text=True, stdin=subprocess.DEVNULL)
        if result.returncode == 0:
            return
        detail = (result.stderr or result.stdout).strip().splitlines()
        failures.append(f"{tool}: {detail[-1] if detail else 'failed'}")
        shutil.rmtree(dest)  # whatever the failed tool left behind must not be imported
        dest.mkdir()
    if not failures:
        raise ArchiveError("rar archives need one of unrar, 7z or bsdtar on PATH")
    raise ArchiveError(f"cannot extract {archive.name} ({'; '.join(failures)})")


def _drop_links(dest: Path) -> None:
    """Archives may carry symlinks; they must never be followed when deploying."""
    for root, dirnames, filenames in os.walk(dest):
        for name in dirnames + filenames:
            path = Path(root, name)
            if path.is_symlink():
                path.unlink()


def extract(archive: Path, dest: Path) -> None:
    """Extract `archive` into the (new or empty) directory `dest`."""
    dest.mkdir(parents=True, exist_ok=True)
    with archive.open("rb") as fh:
        magic = fh.read(8)
    try:
        if magic.startswith(_SEVENZIP_MAGIC):
            _extract_7z(archive, dest)
        elif magic.startswith(_RAR_MAGIC):
            _extract_rar(archive, dest)
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
