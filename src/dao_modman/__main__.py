"""Command line entry point: the TUI by default, plus `nxm://` link handling."""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from .core import laa, nexus
from .core.config import Config, load_config
from .core.install import install_from_nexus
from .core.library import Library, LibraryError
from .core.nexus import NexusClient, NexusError

DESKTOP_FILE = "dao-modman-nxm.desktop"
DESKTOP_ENTRY = """[Desktop Entry]
Type=Application
Name=dao-modman (nxm links)
Exec="{python}" -m dao_modman nxm --wait %u
Terminal=true
NoDisplay=true
MimeType=x-scheme-handler/nxm;
"""


async def _install_link(config: Config, url: str) -> str:
    link = nexus.parse_nxm(url)
    if link.game != nexus.GAME:
        raise NexusError(f"that link is for {link.game!r}, not {nexus.GAME!r}")
    if not config.nexus_token:
        raise NexusError("no Nexus API token: set NEXUS_API_TOKEN (see README)")

    def progress(done: int, total: int | None) -> None:
        of_total = f" / {total / 1e6:.1f}" if total else ""
        print(f"\r{done / 1e6:.1f}{of_total} MB", end="", flush=True)

    client = NexusClient(config.nexus_token)
    try:
        mod, updated = await install_from_nexus(
            client, Library(config.dirs), link.mod_id, link.file_id, link=link, progress=progress
        )
    finally:
        await client.aclose()
    return f"\n{'Updated' if updated else 'Installed'} {mod.name} ({mod.id})"


def cmd_nxm(url: str, wait: bool) -> int:
    status = 0
    try:
        print(asyncio.run(_install_link(load_config(), url)))
    except (NexusError, LibraryError, OSError) as exc:
        print(f"\nerror: {exc}", file=sys.stderr)
        status = 1
    if wait:
        input("Press enter to close ")
    return status


def cmd_install_nxm_handler() -> int:
    applications = Path.home() / ".local/share/applications"
    applications.mkdir(parents=True, exist_ok=True)
    desktop = applications / DESKTOP_FILE
    desktop.write_text(DESKTOP_ENTRY.format(python=sys.executable), encoding="utf-8")
    print(f"wrote {desktop}")
    if not shutil.which("xdg-mime"):
        print("xdg-mime not found: register the nxm:// handler manually", file=sys.stderr)
        return 1
    subprocess.run(["xdg-mime", "default", DESKTOP_FILE, "x-scheme-handler/nxm"], check=True)
    print("registered as handler for nxm:// links")
    return 0


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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="dao-modman", description=__doc__)
    commands = parser.add_subparsers(dest="command")
    nxm = commands.add_parser("nxm", help="download and import a 'Mod Manager Download' link")
    nxm.add_argument("url")
    nxm.add_argument("--wait", action="store_true", help="wait for enter before exiting")
    commands.add_parser("install-nxm-handler", help="register dao-modman for nxm:// links")
    laa_cmd = commands.add_parser("laa", help="Large Address Aware patch for the game exe")
    laa_cmd.add_argument("action", choices=["status", "patch", "restore"])
    laa_cmd.add_argument(
        "--exe", type=Path, help="patched executable to use instead of the bundled"
    )
    args = parser.parse_args(argv)

    if args.command == "nxm":
        return cmd_nxm(args.url, args.wait)
    if args.command == "laa":
        return cmd_laa(args.action, args.exe)
    if args.command == "install-nxm-handler":
        return cmd_install_nxm_handler()

    from .tui.app import ModManApp

    ModManApp(load_config()).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
