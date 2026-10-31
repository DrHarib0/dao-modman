"""Command line entry point: the TUI by default, plus `nxm://` link handling."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .core import console, deploy, ipc, laa, nexus, paths, report, revisions, terminal
from .core.config import (
    TOKEN_FILE,
    TOKEN_VAR,
    Config,
    ConfigError,
    load_config,
    load_token,
    token_warning,
)
from .core.library import Library
from .core.nexus import NexusError

DESKTOP_FILE = "dao-modman-nxm.desktop"
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=dao-modman (nxm links)
Exec="{python}" -m dao_modman nxm %u
Terminal=false
NoDisplay=true
MimeType=x-scheme-handler/nxm;
"""


def _report(message: str) -> None:
    """The handler has no terminal of its own, so errors also go to the desktop."""
    print(f"error: {message}", file=sys.stderr)
    if shutil.which("notify-send"):
        subprocess.run(["notify-send", "--app-name=dao-modman", "dao-modman", message], check=False)


def _forward(config: Config, url: str) -> bool:
    """Hand the link to the running instance, if there is one."""
    if config.runtime_dir is None or not ipc.is_running(config.runtime_dir):
        return False
    ipc.send_link(config.runtime_dir, url)
    return True


def cmd_nxm(url: str) -> int:
    """Pass a link to the running instance, or start one in a new terminal window."""
    config = load_config()
    try:
        nexus.parse_nxm(url)
        if not _forward(config, url):
            argv = [sys.executable, "-m", "dao_modman", "--nxm", url]
            subprocess.Popen(
                terminal.command(config.terminal, argv),
                start_new_session=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except (NexusError, ipc.IpcError, terminal.TerminalError, OSError) as exc:
        _report(str(exc))
        return 1
    return 0


def cmd_install_nxm_handler() -> int:
    applications = Path.home() / ".local/share/applications"
    applications.mkdir(parents=True, exist_ok=True)
    desktop = applications / DESKTOP_FILE
    desktop.write_text(DESKTOP_ENTRY.format(python=sys.executable), encoding="utf-8")
    print(f"wrote {desktop}")
    if not shutil.which("xdg-mime"):
        print("xdg-mime not found: register the nxm:// handler manually", file=sys.stderr)
        return 1
    # On KDE, xdg-mime calls qtpaths, which some distros keep outside PATH.
    env = {**os.environ, "PATH": f"{os.environ.get('PATH', '')}:/usr/lib/qt6/bin"}
    command = ["xdg-mime", "default", DESKTOP_FILE, "x-scheme-handler/nxm"]
    subprocess.run(command, check=True, env=env)
    print("registered as handler for nxm:// links")
    # The browser starts the handler: it rarely sees the variables set in a shell.
    config_dir = paths.config_dir()
    if not load_token(config_dir, environment=False):
        print(
            f"note: the handler may not find your API token: put it into "
            f"{config_dir / TOKEN_FILE} (or set {TOKEN_VAR} for your desktop session)"
        )
    if warning := token_warning(config_dir):
        print(f"warning: {warning}")
    return 0


def _library(config: Config) -> Library:
    return Library(config.dirs, config.ignore, unrar_command=config.unrar_command)


def cmd_laa(action: str, exe: Path | None) -> int:
    install_dir = load_config().game_install_dir
    if not install_dir:
        print(
            "error: game install dir not found: set game_install_dir in config.toml",
            file=sys.stderr,
        )
        return 1
    try:
        if action == "patch":
            if exe is None:
                print(
                    "error: pass the LAA-patched executable with --exe PATH; how to get one:\n"
                    f"  {laa.GUIDE_URL}",
                    file=sys.stderr,
                )
                return 1
            backup = laa.patch(install_dir, exe)
            print(f"patched {laa.game_exe(install_dir)}\noriginal saved as {backup}")
        elif action == "restore":
            print(f"restored {laa.restore(install_dir)}")
        else:
            print(f"{laa.game_exe(install_dir)}: {laa.status(install_dir)}")
    except laa.LaaError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_console(action: str) -> int:
    config = load_config()
    if not config.game_install_dir:
        print(
            "error: game install dir not found: set game_install_dir in config.toml",
            file=sys.stderr,
        )
        return 1
    try:
        if action == "enable":
            console.enable(config.game_install_dir)
            key = console.ensure_key(config.game_user_dir) if config.game_user_dir else None
            print(f"console enabled, open it with: {console.key_label(key)}")
            if not console.fonts_installed(config.game_user_dir, _library(config)):
                print(f"console text is invisible without fonts.erf: {console.FONTS_MOD_URL}")
        elif action == "disable":
            console.disable(config.game_install_dir)
            print("console disabled")
        else:
            state = "enabled" if console.is_enabled(config.game_install_dir) else "disabled"
            print(f"console: {state}")
    except console.ConsoleError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


def cmd_conflicts() -> int:
    config = load_config()
    library = _library(config)
    user_dir = config.game_user_dir
    foreign = (
        deploy.unmanaged(user_dir, config.dirs.deployed_file, config.game_install_dir)
        if user_dir and user_dir.is_dir()
        else None
    )
    plan = deploy.build_plan(library, foreign)
    print(report.format_conflicts(library, plan) or "no conflicts")
    return 0


def cmd_chargen() -> int:
    config = load_config()
    result, text = report.chargen_report(config.game_install_dir, _library(config))
    print(text)
    return 1 if result and result.dangling else 0


def cmd_revisions() -> int:
    data_dir = load_config().dirs.data
    if not revisions.init(data_dir):
        print("error: could not create a git repository (is git installed?)", file=sys.stderr)
        return 1
    print(f"tracking the library's metadata in {data_dir}")
    print("every change is now committed there; look at it with:")
    print(f"  git -C '{data_dir}' log --stat")
    print(f"to stop, delete {data_dir / '.git'}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dao-modman", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    parser.add_argument("--nxm", metavar="URL", help="start the TUI and download this link")
    nxm = commands.add_parser("nxm", help="hand a 'Mod Manager Download' link to the TUI")
    nxm.add_argument("url")
    commands.add_parser("install-nxm-handler", help="register dao-modman for nxm:// links")
    laa_cmd = commands.add_parser("laa", help="Large Address Aware patch for the game exe")
    laa_cmd.add_argument("action", choices=["status", "patch", "restore"])
    laa_cmd.add_argument("--exe", type=Path, help="LAA-patched executable to install (for patch)")
    console_cmd = commands.add_parser("console", help="enable the game's developer console")
    console_cmd.add_argument("action", choices=["status", "enable", "disable"])
    commands.add_parser(
        "conflicts", help="report files that several mods ship, and whose copy is used"
    )
    revisions_cmd = commands.add_parser(
        "revisions", help="keep a history of the library's metadata in git (debugging aid)"
    )
    revisions_cmd.add_argument("action", choices=["init"])
    commands.add_parser(
        "chargen", help="report on the merged character creator list and its broken entries"
    )
    args = parser.parse_args(argv)
    try:
        return _run(args)
    except ConfigError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


def _run(args: argparse.Namespace) -> int:
    if args.command == "nxm":
        return cmd_nxm(args.url)
    if args.command == "chargen":
        return cmd_chargen()
    if args.command == "revisions":
        return cmd_revisions()
    if args.command == "conflicts":
        return cmd_conflicts()
    if args.command == "console":
        return cmd_console(args.action)
    if args.command == "laa":
        return cmd_laa(args.action, args.exe)
    if args.command == "install-nxm-handler":
        return cmd_install_nxm_handler()

    config = load_config()
    if args.nxm:
        # Lost the race against another instance that is starting: let that one handle it.
        try:
            if _forward(config, args.nxm):
                return 0
        except ipc.IpcError as exc:
            _report(str(exc))
            return 1

    from .tui.app import ModManApp

    ModManApp(config, links=[args.nxm] if args.nxm else []).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
