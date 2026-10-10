"""Main screen and key bindings."""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

import httpx
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import DataTable, Footer, Header, ProgressBar, Static

from ..core import addins, console, deploy, laa, pathops
from ..core import archive as archives
from ..core.config import Config
from ..core.dazip import AddIn
from ..core.install import install_from_nexus, link_to_nexus
from ..core.library import Library, LibraryError
from ..core.metadata import MetadataError
from ..core.models import Mod, PathOp, ProfileEntry
from ..core.nexus import NexusClient, NexusError, NxmLink, newest_file_id
from .screens import (
    ConfirmScreen,
    NexusAddScreen,
    PathOpsScreen,
    TextPromptScreen,
    ToolsScreen,
)

ENABLED = "✓"
COLUMNS = ("#", "On", "Name", "Version", "Group", "Kind", "Source", "Ops", "Notes")


class ModManApp(App[None]):
    TITLE = "dao-modman"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding("space", "toggle", "On/off"),
        Binding("K,ctrl+up", "move(-1)", "Up"),
        Binding("J,ctrl+down", "move(1)", "Down"),
        Binding("g", "group", "Group"),
        Binding("n", "add_nexus", "Nexus"),
        Binding("i", "import_file", "Import"),
        Binding("e", "edit_paths", "Paths"),
        Binding("c", "check_updates", "Check"),
        Binding("u", "update", "Update"),
        Binding("a", "apply", "Apply"),
        Binding("x", "unapply", "Unapply"),
        Binding("h", "toggle_official", "DLC"),
        Binding("t", "tools", "Tools"),
        Binding("delete", "remove", "Remove"),
        Binding("q", "quit", "Quit"),
    ]

    def __init__(self, config: Config, *, transport: httpx.AsyncBaseTransport | None = None):
        super().__init__()
        self.config = config
        self.library = Library(config.dirs)
        self.nexus = (
            NexusClient(config.nexus_token, transport=transport) if config.nexus_token else None
        )
        self.account: dict | None = None
        self.updates: set[str] = set()
        self._addin_row_count = 0
        self._addins_error_shown = False

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="mods", cursor_type="row", zebra_stripes=True)
        yield ProgressBar(id="progress")
        yield Static("", id="status", markup=False)
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#mods", DataTable).add_columns(*COLUMNS)
        self.refresh_table()
        for error in self.library.errors:
            self.notify(error, severity="error", timeout=15, markup=False)
        if self.nexus:
            self.load_account()

    async def on_unmount(self) -> None:
        if self.nexus:
            await self.nexus.aclose()

    def addin_rows(self) -> list[tuple[AddIn, str]]:
        """AddIns registered with the game that are not ours: official DLC and foreign ones."""
        if not self.config.game_user_dir:
            return []
        try:
            registered = addins.list_registered(self.config.game_user_dir)
        except addins.AddInsError as exc:
            if not self._addins_error_shown:
                self._addins_error_shown = True
                self.error(exc)
            return []
        managed = {uid for mod in self.library.mods.values() for uid in mod.addin_uids}
        with contextlib.suppress(deploy.DeployError):
            record = deploy.load_record(self.config.dirs.deployed_file) or {}
            managed |= set(record.get("addins", []))
        shipped = addins.shipped_uids(self.config.game_install_dir)
        rows = []
        for addin in registered:
            kind = addins.classify(addin, managed, shipped)
            hidden = kind == addins.OFFICIAL and self.library.profile.hide_official
            if kind != addins.MANAGED and not hidden:
                rows.append((addin, kind))
        return rows

    @staticmethod
    def _cells(enabled: bool, cells: tuple[str, ...]) -> list[Text]:
        """Table cells with the `On` mark inserted; disabled rows are dimmed."""
        number, *rest = cells
        style = "" if enabled else "dim"
        return [Text(cell, style=style) for cell in (number, ENABLED if enabled else "", *rest)]

    def refresh_table(self, select: str | None = None) -> None:
        table = self.query_one("#mods", DataTable)
        selected = select or (entry.id if (entry := self.current_entry()) else None)
        try:
            conflicting = deploy.build_plan(self.library).conflicting_mods()
        except (pathops.PathOpError, OSError) as exc:
            conflicting = set()
            self.notify(str(exc), severity="error", markup=False)
        table.clear()
        shipped = addins.shipped_uids(self.config.game_install_dir)
        addin_rows = self.addin_rows()
        self._addin_row_count = len(addin_rows)
        for addin, kind in addin_rows:
            source = "game" if addin.uid.casefold() in shipped else "user dir"
            cells = ("", addin.name, addin.version, "", kind, source, "", "")
            table.add_row(*self._cells(addin.enabled, cells), key=f"addin:{addin.uid}")
        for index, (entry, mod) in enumerate(self.library.ordered(), 1):
            notes = [
                note
                for note, active in (
                    ("conflict", entry.enabled and mod.id in conflicting),
                    ("update", mod.id in self.updates),
                )
                if active
            ]
            cells = (
                str(index),
                mod.name,
                mod.version,
                entry.group,
                mod.kind,
                f"nexus:{mod.nexus.mod_id}" if mod.nexus else "local",
                str(len(mod.ops)) if mod.ops else "",
                ", ".join(notes),
            )
            table.add_row(*self._cells(entry.enabled, cells), key=mod.id)
        ids = [entry.id for entry in self.library.profile.mods]
        if selected in ids:
            table.move_cursor(row=self._addin_row_count + ids.index(selected))
        elif ids:
            table.move_cursor(row=self._addin_row_count)  # start on the first mod
        self.refresh_status()

    def refresh_status(self) -> None:
        game = str(self.config.game_user_dir or "game user dir not found (set it in config.toml)")
        if self.nexus is None:
            account = "no API token"
        elif self.account is None:
            account = "..."
        else:
            kind = "premium" if self.account.get("is_premium") else "free"
            account = f"{self.account.get('name', '?')} ({kind})"
        deployed = "yes" if self.config.dirs.deployed_file.exists() else "no"
        self.query_one("#status", Static).update(
            f"Game: {game}  |  Nexus: {account}  |  Applied: {deployed}"
        )

    def current_entry(self) -> ProfileEntry | None:
        table = self.query_one("#mods", DataTable)
        entries = self.library.profile.mods
        index = table.cursor_row - self._addin_row_count  # AddIn rows come first
        if table.row_count and 0 <= index < len(entries):
            return entries[index]
        return None

    def current_mod(self) -> Mod | None:
        entry = self.current_entry()
        return self.library.mods[entry.id] if entry else None

    def error(self, exc: Exception | str) -> None:
        self.notify(str(exc), severity="error", timeout=10, markup=False)

    # -- profile actions --------------------------------------------------

    def action_toggle(self) -> None:
        if entry := self.current_entry():
            entry.enabled = not entry.enabled
            self.library.save_profile()
            self.refresh_table()

    def action_toggle_official(self) -> None:
        profile = self.library.profile
        profile.hide_official = not profile.hide_official
        self.library.save_profile()
        self.refresh_table()

    def action_move(self, delta: int) -> None:
        entry = self.current_entry()
        if entry and self.library.profile.move(entry.id, delta):
            self.library.save_profile()
            self.refresh_table(select=entry.id)

    def action_group(self) -> None:
        entry = self.current_entry()
        if not entry:
            return

        def done(group: str | None) -> None:
            if group is not None:
                entry.group = group
                self.library.save_profile()
                self.refresh_table()

        self.push_screen(TextPromptScreen("Group (empty for none)", entry.group), done)

    def action_edit_paths(self) -> None:
        mod = self.current_mod()
        if not mod:
            return
        files = archives.list_files(self.library.files_dir(mod.id))

        def done(ops: list[PathOp] | None) -> None:
            if ops is not None:
                mod.ops = ops
                self.library.save_mod(mod)
                self.refresh_table()

        self.push_screen(PathOpsScreen(mod, pathops.default_mapping(mod, files)), done)

    def action_remove(self) -> None:
        mod = self.current_mod()
        if not mod:
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.library.remove(mod.id)
                self.updates.discard(mod.id)
                self.refresh_table()

        message = f"Remove {mod.name!r} from the library?\nThe archive in downloads/ is kept."
        self.push_screen(ConfirmScreen(message), done)

    # -- importing --------------------------------------------------------

    def action_import_file(self) -> None:
        def done(path: str | None) -> None:
            if path:
                self.import_file(Path(path))

        self.push_screen(TextPromptScreen("Import archive", placeholder="/path/to/mod.zip"), done)

    @work
    async def import_file(self, path: Path) -> None:
        try:
            mod = await asyncio.to_thread(self.library.import_archive, path)
        except (LibraryError, OSError) as exc:
            self.error(exc)
            return
        linked = self.nexus is not None and await link_to_nexus(self.nexus, self.library, mod)
        self.refresh_table(select=mod.id)
        self.notify(f"Imported {mod.name}" + (" (found on Nexus)" if linked else ""), markup=False)

    # -- nexus ------------------------------------------------------------

    @work
    async def load_account(self) -> None:
        try:
            self.account = await self.nexus.validate()
        except NexusError as exc:
            self.error(exc)
            return
        self.refresh_status()

    def action_add_nexus(self) -> None:
        if not self.nexus:
            self.error("No Nexus API token: set NEXUS_API_TOKEN (see README)")
            return

        def done(choice: tuple[int, int, NxmLink | None] | None) -> None:
            if choice:
                self.install(*choice)

        premium = bool(self.account and self.account.get("is_premium"))
        self.push_screen(NexusAddScreen(self.nexus, premium), done)

    @work
    async def install(self, mod_id: int, file_id: int, link: NxmLink | None = None) -> None:
        bar = self.query_one("#progress", ProgressBar)
        bar.update(total=None, progress=0)
        bar.display = True

        def progress(done: int, total: int | None) -> None:
            bar.update(total=total, progress=done)

        try:
            mod, updated = await install_from_nexus(
                self.nexus, self.library, mod_id, file_id, link=link, progress=progress
            )
        except (NexusError, LibraryError, OSError) as exc:
            self.error(exc)
            return
        finally:
            bar.display = False
        self.updates.discard(mod.id)
        self.refresh_table(select=mod.id)
        self.notify(f"{'Updated' if updated else 'Installed'} {mod.name}", markup=False)

    @work(exclusive=True, group="updates")
    async def action_check_updates(self) -> None:
        if not self.nexus:
            self.error("No Nexus API token: set NEXUS_API_TOKEN (see README)")
            return
        listings: dict[int, dict] = {}
        self.updates = set()
        try:
            for mod in list(self.library.mods.values()):
                if not mod.nexus:
                    continue
                if mod.nexus.mod_id not in listings:
                    listings[mod.nexus.mod_id] = await self.nexus.files(mod.nexus.mod_id)
                if (
                    newest_file_id(listings[mod.nexus.mod_id], mod.nexus.file_id)
                    != mod.nexus.file_id
                ):
                    self.updates.add(mod.id)
        except NexusError as exc:
            self.error(exc)
        self.refresh_table()
        self.notify(f"{len(self.updates)} update(s) available")

    @work(exclusive=True, group="updates")
    async def action_update(self) -> None:
        mod = self.current_mod()
        if not mod or not mod.nexus or not self.nexus:
            return
        try:
            listing = await self.nexus.files(mod.nexus.mod_id)
        except NexusError as exc:
            self.error(exc)
            return
        newest = newest_file_id(listing, mod.nexus.file_id)
        if newest == mod.nexus.file_id:
            self.notify(f"{mod.name} is up to date", markup=False)
            return
        self.install(mod.nexus.mod_id, newest)

    # -- deployment -------------------------------------------------------

    def action_apply(self) -> None:
        user_dir = self.config.game_user_dir
        if not user_dir:
            self.error("Game user dir not found: set game_user_dir in config.toml")
            return
        try:
            plan = deploy.build_plan(self.library)
        except (pathops.PathOpError, MetadataError, OSError) as exc:
            self.error(exc)
            return
        lines = [f"Apply {len(plan.files)} file(s) to\n{user_dir} ?"]
        lines += [f"\nwarning: {warning}" for warning in plan.warnings]
        lines += [
            f"\n{target}\n  merged from: {', '.join(mods)}" for target, mods in plan.merged.items()
        ]
        if plan.addins:
            lines.append(f"\nAddIns: {', '.join(addin.name for addin in plan.addins)}")
        if plan.conflicts:
            lines.append(f"\n{len(plan.conflicts)} conflicting file(s), last mod wins:")
            lines += [
                f"  {target}: {' < '.join(mods)}"
                for target, mods in list(plan.conflicts.items())[:8]
            ]
            if len(plan.conflicts) > 8:
                lines.append("  ...")

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_apply(plan, user_dir)

        self.push_screen(ConfirmScreen("\n".join(lines)), done)

    @work
    async def run_apply(self, plan: deploy.Plan, user_dir: Path) -> None:
        try:
            result = await asyncio.to_thread(
                deploy.apply, plan, user_dir, self.config.dirs.deployed_file
            )
        except (deploy.DeployError, OSError, ValueError) as exc:
            self.error(exc)
            return
        finally:
            self.refresh_status()
        message = f"Applied {result.written} file(s)"
        if result.skipped:
            message += f", left {len(result.skipped)} existing file(s) untouched"
        self.notify(message)

    def action_unapply(self) -> None:
        if not self.config.dirs.deployed_file.exists():
            self.notify("Nothing is applied")
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_unapply()

        self.push_screen(ConfirmScreen("Remove all applied mod files from the game?"), done)

    @work
    async def run_unapply(self) -> None:
        try:
            result = await asyncio.to_thread(deploy.unapply, self.config.dirs.deployed_file)
        except (deploy.DeployError, OSError, ValueError) as exc:
            self.error(exc)
            return
        finally:
            self.refresh_status()
        message = f"Removed {result.removed} file(s)"
        if result.kept:
            message += f", kept {len(result.kept)} modified file(s)"
        self.notify(message)

    # -- tools ------------------------------------------------------------

    def action_tools(self) -> None:
        install_dir = self.config.game_install_dir
        try:
            state = laa.status(install_dir) if install_dir else "game install dir not found"
        except laa.LaaError as exc:
            state = str(exc)
        tools = [("laa_restore", "Restore the original game executable")]
        if state != laa.PATCHED:
            tools.insert(0, ("laa_patch", f"Apply the LAA (4 GB) patch - currently {state}"))
        try:
            console_on = bool(install_dir) and console.is_enabled(install_dir)
        except console.ConsoleError:
            console_on = False
        if console_on:
            tools.append(("console_disable", "Disable the developer console"))
        else:
            tools.append(("console_enable", "Enable the developer console"))

        def done(tool: str | None) -> None:
            if tool:
                self.run_tool(tool)

        self.push_screen(ToolsScreen(tools), done)

    def run_tool(self, tool: str) -> None:
        install_dir = self.config.game_install_dir
        if not install_dir:
            self.error("Game install dir not found: set game_install_dir in config.toml")
            return
        try:
            if tool == "laa_patch":
                backup = laa.patch(install_dir)
                self.notify(f"LAA patch applied, original saved as {backup.name}", markup=False)
            elif tool == "laa_restore":
                laa.restore(install_dir)
                self.notify("Original game executable restored")
            elif tool == "console_enable":
                self.enable_console(install_dir)
            elif tool == "console_disable":
                console.disable(install_dir)
                self.notify("Developer console disabled")
        except (laa.LaaError, console.ConsoleError) as exc:
            self.error(exc)

    def enable_console(self, install_dir: Path) -> None:
        console.enable(install_dir)
        user_dir = self.config.game_user_dir
        key = console.ensure_key(user_dir) if user_dir else None
        self.notify(
            f"Developer console enabled, open it with: {console.key_label(key)}", markup=False
        )
        if console.fonts_installed(user_dir, self.library):
            return

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.open_url(console.FONTS_MOD_URL)

        self.push_screen(
            ConfirmScreen(
                "Without fonts.erf, what you type in the console stays invisible.\n\n"
                'Open the Nexus page of "Make CONSOLE commands visible" to install it?\n'
                'Use "Mod Manager Download" there and add the link with n.'
            ),
            done,
        )
