"""Command line entry point: the TUI by default, plus `nxm://` link handling."""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .core import console, deploy, ipc, laa, nexus, paths, report, revisions, terminal
from .core.config import TOKEN_VAR, Config, load_config, load_token
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
    # The browser starts the handler outside the project dir, where ./.env is not found.
    config_dir = paths.config_dir()
    if not os.environ.get(TOKEN_VAR) and not load_token(config_dir, cwd=config_dir):
        print(
            f"note: the handler will not find your API token: put {TOKEN_VAR}=... "
            f"into {config_dir / '.env'}"
        )
    return 0


def _library(config: Config) -> Library:
    return Library(config.dirs, config.ignore)


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
            backup = laa.patch(install_dir, exe)
            print(f"patched {laa.game_exe(install_dir)}\noriginal saved as {backup}")
        elif action == "restore":
            print(f"restored {laa.restore(install_dir)}")
        else:
            print(f"{laa.game_exe(install_dir)}: {laa.status(install_dir)}")
            print(f"bundled LAA executable: {laa.bundled_exe() or 'missing'}")
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


def cmd_conflicts(action: str | None, names: list[str], accept_all: bool) -> int:
    library = _library(load_config())
    plan = deploy.build_plan(library)
    if action is None:
        print(report.format_conflicts(library, plan) or "no conflicts")
        return 1 if plan.with_status(deploy.ERROR) else 0

    if bool(names) == accept_all:
        print("error: give file names or mod ids to accept, or --all (not both)", file=sys.stderr)
        return 2
    selected, unmatched = report.select_conflicts(plan, names)
    if unmatched:
        print(f"error: no open conflict matches {', '.join(unmatched)}", file=sys.stderr)
        return 1
    for conflict in selected:
        *losers, winner = conflict.mod_ids
        library.accept_overwrite(winner, losers, conflict.name)
        print(f"accepted {conflict.name}: {winner} overwrites {', '.join(losers)}")
    if not selected:
        print("no open conflicts")
    for conflict in plan.with_status(deploy.ERROR):
        print(f"not accepted: {report.file_line(library, conflict)}")
    return 0


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
    laa_cmd.add_argument(
        "--exe", type=Path, help="patched executable to use instead of the bundled"
    )
    console_cmd = commands.add_parser("console", help="enable the game's developer console")
    console_cmd.add_argument("action", choices=["status", "enable", "disable"])
    conflicts = commands.add_parser(
        "conflicts", help="report files that several mods ship, or accept them as intended"
    )
    conflicts.add_argument("action", nargs="?", choices=["accept"])
    conflicts.add_argument(
        "names", nargs="*", help="with accept: file names or mod ids of the conflicts to accept"
    )
    conflicts.add_argument("--all", action="store_true", help="with accept: every open conflict")
    revisions_cmd = commands.add_parser(
        "revisions", help="keep a history of the library's metadata in git (debugging aid)"
    )
    revisions_cmd.add_argument("action", choices=["init"])
    args = parser.parse_args(argv)

    if args.command == "nxm":
        return cmd_nxm(args.url)
    if args.command == "revisions":
        return cmd_revisions()
    if args.command == "conflicts":
        return cmd_conflicts(args.action, args.names, args.all)
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
