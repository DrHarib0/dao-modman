"""Register dao-modman as the handler for nxm:// links, or remove it again.

On Linux a desktop file declares the scheme and xdg-mime makes it the default; on Windows a
per-user registry key does both. Either way the browser then runs `dao-modman nxm <link>`.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from . import paths, windows

WINDOWS = sys.platform == "win32"

DESKTOP_FILE = "dao-modman-nxm.desktop"
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=dao-modman (nxm links)
Exec="{python}" -m dao_modman nxm %u
Terminal=false
NoDisplay=true
MimeType=x-scheme-handler/nxm;
"""
MIME_TYPE = "x-scheme-handler/nxm"

REGISTRY_KEY = r"Software\Classes\nxm"
REGISTRY_COMMAND = rf"{REGISTRY_KEY}\shell\open\command"
MARK = "dao_modman"  # in our command line, and in nobody else's


class HandlerError(Exception):
    pass


OURS = "ours"
OTHER = "other"  # another program handles nxm:// links
NONE = "none"


def status() -> tuple[str, str]:
    """Who handles nxm:// links: (OURS | OTHER | NONE, the other program's command or entry)."""
    if WINDOWS:
        current = windows.registry_value(REGISTRY_COMMAND) or ""
        if not current:
            return NONE, ""
        return (OURS, "") if MARK in current else (OTHER, current)
    default = ""
    if shutil.which("xdg-mime"):
        try:
            result = subprocess.run(
                ["xdg-mime", "query", "default", MIME_TYPE],
                capture_output=True,
                text=True,
                timeout=5,
            )
            default = result.stdout.strip()
        except (OSError, subprocess.SubprocessError):
            pass
    if default == DESKTOP_FILE or (not default and _desktop_file().is_file()):
        return OURS, ""
    return (OTHER, default) if default else (NONE, "")


def install() -> list[str]:
    """Register the handler; returns what was done, for the user."""
    return _install_windows() if WINDOWS else _install_linux()


def uninstall() -> list[str]:
    """Remove the handler, if it is ours; returns what was done, for the user."""
    return _uninstall_windows() if WINDOWS else _uninstall_linux()


def _desktop_file() -> Path:
    return Path.home() / ".local/share/applications" / DESKTOP_FILE


def _mimeapps() -> Path:
    return paths._xdg("XDG_CONFIG_HOME", ".config") / "mimeapps.list"


def _install_linux() -> list[str]:
    desktop = _desktop_file()
    desktop.parent.mkdir(parents=True, exist_ok=True)
    desktop.write_text(DESKTOP_ENTRY.format(python=sys.executable), encoding="utf-8")
    done = [f"wrote {desktop}"]
    if not shutil.which("xdg-mime"):
        raise HandlerError(f"{done[0]}, but xdg-mime was not found: register it manually")
    # On KDE, xdg-mime calls qtpaths, which some distros keep outside PATH.
    env = {**os.environ, "PATH": f"{os.environ.get('PATH', '')}:/usr/lib/qt6/bin"}
    try:
        subprocess.run(["xdg-mime", "default", DESKTOP_FILE, MIME_TYPE], check=True, env=env)
    except (OSError, subprocess.CalledProcessError) as exc:
        raise HandlerError(f"xdg-mime failed: {exc}") from exc
    return [*done, "registered as handler for nxm:// links"]


def _uninstall_linux() -> list[str]:
    done = []
    desktop = _desktop_file()
    if desktop.is_file():
        desktop.unlink()
        done.append(f"removed {desktop}")
    mimeapps = _mimeapps()
    try:
        text = mimeapps.read_text(encoding="utf-8")
    except OSError:
        text = ""
    lines, changed = [], False
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if sep and key.strip() == MIME_TYPE:
            handlers = [h for h in value.strip().split(";") if h]
            if DESKTOP_FILE in handlers:
                changed = True
                handlers.remove(DESKTOP_FILE)
                if not handlers:
                    continue  # nothing else handles nxm:// here
                line = f"{key}={';'.join(handlers)};"
        lines.append(line)
    if changed:
        mimeapps.write_text("\n".join(lines) + "\n", encoding="utf-8")
        done.append(f"removed dao-modman as nxm:// handler from {mimeapps}")
    return done or ["dao-modman was not registered as nxm:// handler"]


def _command_line() -> str:
    return f'"{windows.windowless_python()}" -m {MARK} nxm "%1"'


def _install_windows() -> list[str]:
    previous = windows.registry_value(REGISTRY_COMMAND, machine=False)
    try:
        windows.set_user_registry(REGISTRY_KEY, {"": "URL:nxm Protocol", "URL Protocol": ""})
        windows.set_user_registry(REGISTRY_COMMAND, {"": _command_line()})
    except OSError as exc:
        raise HandlerError(f"cannot write the registry: {exc}") from exc
    done = [f"registered as handler for nxm:// links (HKEY_CURRENT_USER\\{REGISTRY_KEY})"]
    if previous and MARK not in previous:
        done.append(f"replaced the previous handler: {previous}")
    return done


def _uninstall_windows() -> list[str]:
    current = windows.registry_value(REGISTRY_COMMAND, machine=False)
    if not current:
        return ["dao-modman was not registered as nxm:// handler"]
    if MARK not in current:
        return [f"left the nxm:// handler alone, it is not dao-modman's: {current}"]
    try:
        windows.delete_user_registry(REGISTRY_KEY)
    except OSError as exc:
        raise HandlerError(f"cannot change the registry: {exc}") from exc
    return [f"removed dao-modman as nxm:// handler (HKEY_CURRENT_USER\\{REGISTRY_KEY})"]
