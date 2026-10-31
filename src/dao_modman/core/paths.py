"""XDG directories and Steam discovery: Proton prefixes on Linux, the registry on Windows."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from . import windows

APP_NAME = "dao-modman"
# Dragon Age: Origins, Dragon Age: Origins - Ultimate Edition
STEAM_APP_IDS = ("17450", "47810")
USER_DIR_IN_DOCUMENTS = Path("BioWare/Dragon Age")
USER_DIR_IN_PREFIX = Path("pfx/drive_c/users/steamuser/Documents") / USER_DIR_IN_DOCUMENTS

_ACF_INSTALLDIR = re.compile(r'"installdir"\s+"((?:[^"\\]|\\.)*)"')
_VDF_PATH = re.compile(r'"path"\s+"((?:[^"\\]|\\.)*)"')


@dataclass(frozen=True)
class Dirs:
    data: Path
    downloads: Path

    @property
    def mods(self) -> Path:
        return self.data / "mods"

    @property
    def profile_file(self) -> Path:
        return self.data / "profile.toml"

    @property
    def deployed_file(self) -> Path:
        return self.data / "deployed.json"

    def ensure(self) -> None:
        for path in (self.data, self.downloads, self.mods):
            path.mkdir(parents=True, exist_ok=True)


def _xdg(var: str, default: str) -> Path:
    value = os.environ.get(var)
    return Path(value) if value else Path.home() / default


def data_dir() -> Path:
    return _xdg("XDG_DATA_HOME", ".local/share") / APP_NAME


def config_dir() -> Path:
    return _xdg("XDG_CONFIG_HOME", ".config") / APP_NAME


def steam_roots() -> list[Path]:
    home = Path.home()
    candidates = [
        _xdg("XDG_DATA_HOME", ".local/share") / "Steam",
        home / ".steam/steam",
        home / ".steam/root",
        home / ".var/app/com.valvesoftware.Steam/.local/share/Steam",
        home / ".var/app/com.valvesoftware.Steam/data/Steam",
        *windows.steam_folders(),
    ]
    roots: list[Path] = []
    for candidate in candidates:
        if not candidate.is_dir():
            continue
        resolved = candidate.resolve()
        if resolved not in roots:
            roots.append(resolved)
    return roots


def steam_libraries(root: Path) -> list[Path]:
    """Library folders known to a Steam installation, including the root itself."""
    libraries = [root]
    vdf = root / "steamapps/libraryfolders.vdf"
    try:
        text = vdf.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return libraries
    for match in _VDF_PATH.finditer(text):
        path = Path(match.group(1).replace("\\\\", "\\"))
        if path.is_dir() and path.resolve() not in libraries:
            libraries.append(path.resolve())
    return libraries


def find_game_user_dir(roots: list[Path] | None = None) -> Path | None:
    """The game's `Documents/BioWare/Dragon Age` dir, if it exists (the game creates it).

    On Linux inside the game's Proton prefix; on Windows in the user's Documents folder,
    whichever store the game came from. Given `roots`, only those Steam installs are searched.
    """
    for root in steam_roots() if roots is None else roots:
        for library in steam_libraries(root):
            for app_id in STEAM_APP_IDS:
                user_dir = library / "steamapps/compatdata" / app_id / USER_DIR_IN_PREFIX
                if user_dir.is_dir():
                    return user_dir
    documents = windows.documents_dir() if roots is None else None
    if documents and (user_dir := documents / USER_DIR_IN_DOCUMENTS).is_dir():
        return user_dir
    return None


def find_game_app_id(roots: list[Path] | None = None) -> str | None:
    """Steam app id of the installed edition (Origins or Ultimate Edition)."""
    for root in steam_roots() if roots is None else roots:
        for library in steam_libraries(root):
            for app_id in STEAM_APP_IDS:
                if (library / "steamapps" / f"appmanifest_{app_id}.acf").is_file():
                    return app_id
    return None


def find_game_install_dir(roots: list[Path] | None = None) -> Path | None:
    """The game's install dir (`steamapps/common/<installdir>`), if the game is installed."""
    for root in steam_roots() if roots is None else roots:
        for library in steam_libraries(root):
            for app_id in STEAM_APP_IDS:
                manifest = library / "steamapps" / f"appmanifest_{app_id}.acf"
                try:
                    match = _ACF_INSTALLDIR.search(manifest.read_text("utf-8", "replace"))
                except OSError:
                    continue
                if match and (install_dir := library / "steamapps/common" / match[1]).is_dir():
                    return install_dir
    return None


def normalize_rel(path: str) -> str:
    """Normalize a user supplied relative path; reject anything that could escape its root."""
    cleaned = path.replace("\\", "/").strip().strip("/")
    parts = [part for part in PurePosixPath(cleaned).parts if part != "."]
    if not parts:
        raise ValueError("empty path")
    if any(part == ".." or "/" in part or "\0" in part or ":" in part for part in parts):
        raise ValueError(f"unsafe path: {path!r}")
    return "/".join(parts)


def resolve_case(root: Path, rel: str) -> Path:
    """Map `rel` below `root`, reusing the casing of entries that already exist on disk.

    The game runs on a case-insensitive filesystem view, so `Packages/Core` and
    `packages/core` must end up in the same directory.
    """
    current = root
    for part in rel.split("/"):
        candidate = current / part
        if not candidate.exists() and current.is_dir():
            folded = part.casefold()
            for entry in current.iterdir():
                if entry.name.casefold() == folded:
                    candidate = entry
                    break
        current = candidate
    return current
