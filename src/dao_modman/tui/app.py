"""Main screen and key bindings."""

from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

import httpx2
from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Header, Input, Label, ProgressBar, Static

from ..core import (
    addins,
    adopt,
    console,
    deploy,
    ipc,
    itemlists,
    laa,
    launch,
    moditems,
    pathops,
    paths,
    report,
    revisions,
    textures,
)
from ..core.config import Config, set_option
from ..core.dazip import AddIn
from ..core.install import install_from_nexus, link_to_nexus
from ..core.library import Library, LibraryError
from ..core.metadata import MetadataError
from ..core.models import Mod, PathOp, ProfileEntry
from ..core.nexus import (
    GAME,
    NexusClient,
    NexusError,
    NxmLink,
    download_page,
    newest_file_id,
    parse_nxm,
)
from .screens import (
    ChoiceScreen,
    ConfirmScreen,
    ConflictsScreen,
    GroupScreen,
    ItemsScreen,
    KeysScreen,
    NexusAddScreen,
    OptionsScreen,
    PathOpsScreen,
    ProgressScreen,
    TextPromptScreen,
    TextScreen,
    UnapplyScreen,
)

ENABLED = "✓"
T = TypeVar("T")

COLUMNS = ("#", "On", "Name", "Version", "Group", "Kind", "Source", "Ops", "Notes")


@dataclass
class _Startup:
    """What the startup checks found."""

    plan: deploy.Plan | None = None
    states: dict[str, str] = field(default_factory=dict)  # mod id -> applied state
    foreign: list[tuple[str, Path]] = field(default_factory=list)
    adoptable: int = 0
    rolled_back: bool = False
    errors: list[str] = field(default_factory=list)


class DownloadRow(Horizontal):
    """One running download: name and progress."""

    def __init__(self, name: str) -> None:
        super().__init__()
        self.label = name

    def compose(self) -> ComposeResult:
        yield Label(self.label, markup=False)
        yield ProgressBar()

    def set_title(self, name: str) -> None:
        self.label = name
        self.query_one(Label).update(name)

    def set_progress(self, done: int, total: int | None) -> None:
        self.query_one(ProgressBar).update(total=total, progress=done)


@dataclass(frozen=True)
class Key:
    """One command of the main screen: its binding, and how it is presented."""

    keys: str  # as for textual's Binding; the first key is the one shown
    action: str
    label: str  # short, for the footer
    help: str  # one line, for the key overview
    section: str
    footer: bool = False  # the footer only has room for the everyday keys
    display: str | None = None  # how to show the key, if not by its name
    footer_display: str | None = None  # in the footer, when one entry stands for two keys


# Both the footer and the key overview (`?`) are built from this table. Footer entries
# appear in this order; together they must fit an 80 column terminal.
KEYS = [
    Key("space", "toggle", "On/off", "Switch the mod on or off", "Mod list", footer=True),
    Key(
        "K,ctrl+up",
        "move(-1)",
        "Move",
        "Move the mod up (lower priority)",
        "Mod list",
        footer=True,
        footer_display="K/J",  # one footer entry for both directions
    ),
    Key("J,ctrl+down", "move(1)", "Down", "Move the mod down (higher priority)", "Mod list"),
    Key("n", "add_nexus", "Nexus", "Add a mod from Nexus (URL, id or nxm:// link)", "Nexus", True),
    Key("a", "apply", "Apply", "Apply the enabled mods to the game", "Game", footer=True),
    Key("x", "unapply", "Unapply", "Remove the applied mods from the game", "Game", footer=True),
    Key("l", "launch", "Launch", "Launch the game through Steam", "Game", footer=True),
    Key("question_mark", "keys", "Keys", "Show this overview", "App", footer=True, display="?"),
    Key("q", "quit", "Quit", "Quit", "App", footer=True),
    Key("r", "rename", "Rename", "Rename the mod", "Mod list"),
    Key("g", "group", "Group", "Assign the mod to a group", "Mod list"),
    Key("delete", "remove", "Remove", "Remove the mod from the library", "Mod list"),
    Key("h", "toggle_official", "DLC", "Hide or show the game's official DLC", "Mod list"),
    Key("slash", "filter", "Filter", "Filter the list by name or group", "Mod list", display="/"),
    Key("escape", "clear_filter", "Unfilter", "Clear the filter", "Mod list", display="esc"),
    Key("e", "edit_paths", "Paths", "Remove or rename the files a mod installs", "Mod contents"),
    Key("p", "options", "Options", "Choose the variants of an override package", "Mod contents"),
    Key("v", "conflicts", "Conflicts", "Show the files the mod shares with others", "Mod contents"),
    Key("L", "items", "Items", "List the items a mod adds, by their in-game names", "Mod contents"),
    Key("i", "import_file", "Import", "Import a local archive", "Nexus"),
    Key("o", "open_page", "Nexus page", "Open the mod's Nexus page in the browser", "Nexus"),
    Key("c", "check_updates", "Check", "Check Nexus for updates", "Nexus"),
    Key("u", "update", "Update", "Update the mod to its newest file", "Nexus"),
    Key(
        "t",
        "tools",
        "Tools",
        "Tools: LAA patch, texture size, all conflicts, console, creator report",
        "Game",
    ),
]
SECTIONS = ("Mod list", "Mod contents", "Nexus", "Game", "App")


def token_hint() -> str:
    """Where to put the Nexus API token, with the real path of the config dir."""
    return f"put NEXUS_API_TOKEN=... into {paths.config_dir() / '.env'}"


def key_overview() -> list[tuple[str, list[tuple[str, str]]]]:
    """The commands by section, as `(key as shown, description)` pairs."""
    return [
        (
            section,
            [
                (key.display or key.keys.partition(",")[0], key.help)
                for key in KEYS
                if key.section == section
            ],
        )
        for section in SECTIONS
    ]


class ModManApp(App[None]):
    TITLE = "dao-modman"
    CSS_PATH = "app.tcss"
    BINDINGS = [
        Binding(
            key.keys,
            key.action,
            key.label,
            show=key.footer,
            key_display=key.footer_display or key.display,
        )
        for key in KEYS
    ]

    def __init__(
        self,
        config: Config,
        *,
        transport: httpx2.AsyncBaseTransport | None = None,
        links: list[str] | None = None,
    ) -> None:
        super().__init__()
        self.config = config
        self.library = Library(config.dirs, config.ignore, load=False)  # loaded by startup
        self.game_data = moditems.GameData()  # read on demand, kept for the session
        self.nexus = (
            NexusClient(config.nexus_token, transport=transport) if config.nexus_token else None
        )
        self.account: dict | None = None
        self.updates: set[str] = set()
        self.foreign: list[tuple[str, Path]] = []  # files in the mod dirs we do not manage
        self._pending = 0  # mods whose files differ from the last apply
        self.filter_text = ""  # only rows whose name or group contains it are shown
        self._row_ids: list[str | None] = []  # the mod of each table row; None: official DLC
        self._busy = False  # a long operation is running behind a progress dialog
        self._addins_error_shown = False
        self._links = links or []  # nxm:// links to download right after start
        self._lock: ipc.InstanceLock | None = None
        self._server: asyncio.Server | None = None
        self.started = asyncio.Event()  # set once the startup checks are done

    # -- layout -----------------------------------------------------------

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(placeholder="filter by name or group", id="filter")
        yield DataTable(id="mods", cursor_type="row", zebra_stripes=True)
        yield Vertical(id="downloads")
        yield Static("", id="status", markup=False)
        yield Footer(show_command_palette=False)

    async def on_mount(self) -> None:
        self.query_one("#mods", DataTable).add_columns(*COLUMNS)
        self.query_one("#downloads").display = False
        self.query_one("#filter").display = False
        self.query_one("#mods").focus()
        self._busy = True  # no keys until the library is loaded
        self.startup()

    @work(exclusive=True, group="startup")
    async def startup(self) -> None:
        """Load and check everything behind a progress dialog, then fill the mod list."""
        result = await self.run_with_progress(
            "Starting dao-modman", self._startup_checks, splash=True
        )
        self.foreign = result.foreign
        self.refresh_table(plan=result.plan, states=result.states)
        for error in [*self.library.errors, *result.errors]:
            self.notify(error, severity="error", timeout=15, markup=False)
        if result.rolled_back:
            self.notify(
                "The last apply did not finish and has been rolled back: nothing is applied",
                severity="warning",
                timeout=15,
            )
        if result.adoptable:
            self.notify(
                f"{result.adoptable} file(s) in the mod directories are not managed by"
                " dao-modman: t \u2192 Adopt takes them over",
                timeout=10,
                markup=False,
            )
        self.started.set()
        if self.nexus:
            self.load_account()
        await self.listen()
        for url in self._links:
            if error := self.receive_link(url):
                self.error(error)

    def _startup_checks(self, progress: deploy.Progress) -> _Startup:
        """Everything the mod list needs at start; runs in a thread. Problems are collected."""
        result = _Startup()
        self.library.reload(progress)
        try:
            result.rolled_back = deploy.rollback(self.config.dirs.deployed_file)
        except (deploy.DeployError, OSError, ValueError) as exc:
            result.errors.append(str(exc))
        user_dir = self.config.game_user_dir
        if user_dir and user_dir.is_dir():
            # after the rollback: an unfinished apply's files are not foreign
            progress("Checking the game directories", 0, 1)
            try:
                result.foreign = deploy.unmanaged(
                    user_dir, self.config.dirs.deployed_file, self.config.game_install_dir
                )
                result.adoptable = adopt.find(
                    self.library,
                    user_dir,
                    self.config.dirs.deployed_file,
                    self.config.game_install_dir,
                    entries=result.foreign,
                ).file_count
            except (deploy.DeployError, addins.AddInsError, OSError) as exc:
                result.errors.append(str(exc))
            progress("Checking the game directories", 1, 1)
        try:
            result.plan = deploy.build_plan(self.library, result.foreign, progress)
            if record := self._record():
                progress("Comparing with the last apply", 0, 1)
                result.states = deploy.compare(result.plan, record, result.foreign).mods
                progress("Comparing with the last apply", 1, 1)
        except (pathops.PathOpError, OSError) as exc:
            result.errors.append(str(exc))
        return result

    async def listen(self) -> None:
        """Become the instance that the nxm:// handler forwards links to, if there is none."""
        directory = self.config.runtime_dir
        if directory is None:
            return
        lock = ipc.InstanceLock(directory)
        if not lock.acquire():
            return  # another instance is already listening
        self._lock = lock
        try:
            self._server = await ipc.serve(directory, self.receive_link)
        except OSError as exc:
            self.error(f"cannot listen for nxm:// links: {exc}")

    async def on_unmount(self) -> None:
        if self._server and self.config.runtime_dir:
            await ipc.stop(self._server, self.config.runtime_dir)
        if self._lock:
            self._lock.release()
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

    def _options_note(self, mod: Mod) -> str:
        """Says that a mod has package options, and how many differ from the defaults."""
        keys = [key for package in self.library.options(mod) for key in package.keys]
        if not keys:
            return ""
        changed = sum(not key.is_default(mod.options) for key in keys)
        return f"options ({changed} changed)" if changed else "options"

    def _overwrites_note(self, mod: Mod) -> str:
        """Names the mod this one is declared to overwrite, or counts them if several."""
        targets = list(dict.fromkeys(overwrite.mod for overwrite in mod.overwrites))
        if len(targets) != 1:
            return f"overwrites {len(targets)} mods"
        other = self.library.mods.get(targets[0])
        return f"overwrites {other.name if other else targets[0] + ' (missing)'}"

    def refresh_table(
        self,
        select: str | None = None,
        *,
        plan: deploy.Plan | None = None,
        states: dict[str, str] | None = None,
    ) -> None:
        """Rebuild the mod list; `plan` and `states` if they were worked out already."""
        table = self.query_one("#mods", DataTable)
        selected = select or (entry.id if (entry := self.current_entry()) else None)
        try:
            if plan is None:
                plan = deploy.build_plan(self.library, self.foreign)
            conflicting, misordered = plan.conflicting_mods(), plan.misordered_mods()
            if states is None:
                record = self._record()  # while nothing is applied, the status line says so
                states = deploy.compare(plan, record, self.foreign).mods if record else {}
        except (pathops.PathOpError, OSError) as exc:
            conflicting, misordered, states = set(), set(), {}
            self.notify(str(exc), severity="error", markup=False)
        self._pending = sum(state != deploy.APPLIED for state in states.values())
        ordered = self.library.ordered()
        if select and not any(self._matches(m.name, e.group) for e, m in ordered if m.id == select):
            self._set_filter("")  # never hide the mod that was just added or changed
        table.clear()
        self._row_ids = []
        shipped = addins.shipped_uids(self.config.game_install_dir)
        for addin, kind in self.addin_rows():
            if not self._matches(addin.name):
                continue
            self._row_ids.append(None)
            source = "game" if addin.uid.casefold() in shipped else "user dir"
            cells = ("", addin.name, addin.version, "", kind, source, "", "")
            table.add_row(*self._cells(addin.enabled, cells), key=f"addin:{addin.uid}")
        for index, (entry, mod) in enumerate(ordered, 1):
            if not self._matches(mod.name, entry.group):
                continue
            self._row_ids.append(mod.id)
            options_note = self._options_note(mod)
            notes = [
                note
                for note, active in (
                    (states.get(mod.id, ""), states.get(mod.id, deploy.APPLIED) != deploy.APPLIED),
                    ("wrong order", mod.id in misordered),
                    ("conflict", entry.enabled and mod.id in conflicting),
                    ("update", mod.id in self.updates),
                    (self._overwrites_note(mod), bool(mod.overwrites)),
                    (options_note, bool(options_note)),
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
            row = self._cells(entry.enabled, cells)
            if mod.id in misordered:
                row[-1].stylize("bold red")
            table.add_row(*row, key=mod.id)
        mod_rows = [row for row, mod_id in enumerate(self._row_ids) if mod_id is not None]
        if selected is not None and selected in self._row_ids:
            table.move_cursor(row=self._row_ids.index(selected))
        elif mod_rows:
            table.move_cursor(row=mod_rows[0])  # start on the first mod
        self.refresh_status()

    def _matches(self, *texts: str) -> bool:
        wanted = self.filter_text.casefold()
        return not wanted or any(wanted in text.casefold() for text in texts)

    def _set_filter(self, text: str) -> None:
        """Change the filter text; the input shows it and is hidden while there is none."""
        self.filter_text = text.strip()
        box = self.query_one("#filter", Input)
        if box.value.strip() != self.filter_text:
            box.value = self.filter_text
        if not self.filter_text and self.focused is not box:
            box.display = False

    def refresh_status(self) -> None:
        if self.nexus is None:
            account = "no API token"
        elif self.account is None:
            account = "..."
        else:
            kind = "premium" if self.account.get("is_premium") else "free"
            account = f"{self.account.get('name', '?')} ({kind})"
        if not self.config.dirs.deployed_file.exists():
            deployed = "no"
        elif self._pending:
            deployed = f"yes, {self._pending} mod(s) to apply"
        else:
            deployed = "yes, up to date"
        status = f"Nexus: {account}  |  Applied: {deployed}"
        if not self.config.game_user_dir:  # worth a place only while it is a problem
            status = f"Game user dir not found (set it in config.toml)  |  {status}"
        if self.filter_text:
            shown = sum(mod_id is not None for mod_id in self._row_ids)
            status = (
                f"Filter: {self.filter_text} ({shown} of {len(self.library.mods)})  |  {status}"
            )
        self.query_one("#status", Static).update(status)

    def _record(self) -> dict | None:
        """The last apply's record; None if nothing is applied or it cannot be read."""
        try:
            return deploy.load_record(self.config.dirs.deployed_file)
        except deploy.DeployError:
            return None

    def current_entry(self) -> ProfileEntry | None:
        row = self.query_one("#mods", DataTable).cursor_row
        mod_id = self._row_ids[row] if 0 <= row < len(self._row_ids) else None
        return self.library.profile.entry(mod_id) if mod_id else None

    def current_mod(self) -> Mod | None:
        entry = self.current_entry()
        return self.library.mods[entry.id] if entry else None

    def error(self, exc: Exception | str) -> None:
        self.notify(str(exc), severity="error", timeout=10, markup=False)

    # -- profile actions --------------------------------------------------

    def action_toggle(self) -> None:
        if entry := self.current_entry():
            entry.enabled = not entry.enabled
            self.library.save_profile(f"{'enable' if entry.enabled else 'disable'} {entry.id}")
            self.refresh_table()

    def action_toggle_official(self) -> None:
        profile = self.library.profile
        profile.hide_official = not profile.hide_official
        self.library.save_profile(f"{'hide' if profile.hide_official else 'show'} official DLC")
        self.refresh_table()

    def action_filter(self) -> None:
        box = self.query_one("#filter", Input)
        box.display = True
        box.focus()

    def action_clear_filter(self) -> None:
        if not self.filter_text and not self.query_one("#filter").display:
            return
        self.query_one("#mods").focus()
        self.query_one("#filter").display = False
        self._set_filter("")
        self.refresh_table()

    @on(Input.Changed, "#filter")
    def filter_changed(self, event: Input.Changed) -> None:
        if event.value.strip() != self.filter_text:
            self.filter_text = event.value.strip()
            self.refresh_table()

    @on(Input.Submitted, "#filter")
    def filter_submitted(self) -> None:
        """Back to the list; the filter stays."""
        self.query_one("#mods").focus()
        self.query_one("#filter").display = bool(self.filter_text)

    def action_move(self, delta: int) -> None:
        if self.filter_text:
            self.notify("Clear the filter (esc) to move mods")
            return
        entry = self.current_entry()
        if entry and self.library.profile.move(entry.id, delta):
            self.library.save_profile(f"move {entry.id} {'down' if delta > 0 else 'up'}")
            self.refresh_table(select=entry.id)

    def action_rename(self) -> None:
        """Change a mod's display name. Its id, and with it its folder names, stay the same."""
        mod = self.current_mod()
        if not mod:
            return

        def done(name: str | None) -> None:
            if name and name != mod.name:
                mod.name = name
                self.library.save_mod(mod, f"{mod.id}: rename to {name}")
                self.refresh_table()

        self.push_screen(TextPromptScreen("Rename mod", mod.name), done)

    def action_group(self) -> None:
        entry = self.current_entry()
        if not entry:
            return

        def done(group: str | None) -> None:
            if group is not None:
                entry.group = group
                self.library.save_profile(f"{entry.id}: set group to {group or '(none)'}")
                self.refresh_table()

        groups = sorted({e.group for e in self.library.profile.mods if e.group}, key=str.casefold)
        self.push_screen(GroupScreen(entry.group, groups), done)

    def action_options(self) -> None:
        """Choose the variants an override package offers."""
        mod = self.current_mod()
        if not mod:
            return
        if not any(package.keys for package in self.library.options(mod)):
            self.notify(f"{mod.name} has no options", markup=False)
            return
        self.push_screen(OptionsScreen(self.library, mod), lambda _: self.refresh_table())

    @work
    async def action_items(self) -> None:
        """List the items the selected mod adds."""
        mod = self.current_mod()
        if not mod:
            return
        try:
            found = await self.run_with_progress(
                "Reading items",
                moditems.collect,
                self.library,
                mod,
                self.config.game_install_dir,
                self.game_data,
                self.config.language,
            )
        except (pathops.PathOpError, MetadataError, OSError) as exc:
            self.error(exc)
            return
        for problem in found.problems[:3]:
            self.notify(problem, severity="warning", markup=False)
        if not found.added and not found.replaced:
            self.notify(f"{mod.name} has no items", markup=False)
            return
        notes = []
        if found.replaced:
            more = "more " if found.added else ""
            notes.append(f"{found.replaced} {more}replace items of the game")
        title = f"{len(found.added)} item(s) added by {mod.name}"
        self.push_screen(ItemsScreen(title, found.added, notes))

    def action_edit_paths(self) -> None:
        mod = self.current_mod()
        if not mod:
            return

        def done(ops: list[PathOp] | None) -> None:
            if ops is not None:
                mod.ops = ops
                self.library.save_mod(mod, f"{mod.id}: edit path operations ({len(ops)})")
                self.refresh_table()

        base = self.library.default_targets(mod, ignored=True)
        ignored = {source for source in base.values() if self.library.ignore.matches(source)}
        screen = PathOpsScreen(mod, base, files_dir=self.library.files_dir(mod.id), ignored=ignored)
        self.push_screen(screen, done)

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

    def action_open_page(self) -> None:
        """Open the selected mod's page on Nexus in the browser."""
        mod = self.current_mod()
        if not mod:
            return
        if not mod.nexus:
            self.notify(f"{mod.name} did not come from Nexus", markup=False)
            return
        self.open_url(f"https://www.nexusmods.com/{mod.nexus.game}/mods/{mod.nexus.mod_id}")

    def action_add_nexus(self) -> None:
        if not self.nexus:
            self.error(f"No Nexus API token: {token_hint()}")
            return

        premium = bool(self.account and self.account.get("is_premium"))

        def done(choice: tuple[int, list[int], NxmLink | None] | None) -> None:
            if not choice:
                return
            mod_id, file_ids, link = choice
            if link is not None or premium:
                for file_id in file_ids:
                    self.install(mod_id, file_id, link)
                return
            # free account: the website hands out the links, the nxm handler brings them back
            for file_id in file_ids:
                self.open_url(download_page(mod_id, file_id))
            self.notify(
                f'Opened {len(file_ids)} download page(s): click "Slow download" on each',
                markup=False,
            )

        installed = {
            (mod.nexus.mod_id, mod.nexus.file_id) for mod in self.library.mods.values() if mod.nexus
        }
        self.push_screen(NexusAddScreen(self.nexus, premium, installed), done)

    def receive_link(self, url: str) -> str | None:
        """Start downloading an nxm:// link. Returns an error message if it is unusable."""
        try:
            link = parse_nxm(url)
        except NexusError as exc:
            return str(exc)
        if link.game != GAME:
            return f"that link is for {link.game!r}, not {GAME!r}"
        if not self.nexus:
            return f"no Nexus API token: {token_hint()}"
        self.install(link.mod_id, link.file_id, link)
        return None

    @work
    async def install(self, mod_id: int, file_id: int, link: NxmLink | None = None) -> None:
        panel = self.query_one("#downloads")
        row = DownloadRow(f"Nexus mod {mod_id}")
        await panel.mount(row)
        panel.display = True
        try:
            mod, updated = await install_from_nexus(
                self.nexus,
                self.library,
                mod_id,
                file_id,
                link=link,
                progress=row.set_progress,
                started=row.set_title,
            )
        except (NexusError, LibraryError, OSError) as exc:
            self.error(exc)
            return
        finally:
            await row.remove()
            panel.display = bool(panel.children)
        self.updates.discard(mod.id)
        self.refresh_table(select=mod.id)
        self.notify(f"{'Updated' if updated else 'Installed'} {mod.name}", markup=False)

    @work(exclusive=True, group="updates")
    async def action_check_updates(self) -> None:
        if not self.nexus:
            self.error(f"No Nexus API token: {token_hint()}")
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

    def action_conflicts(self) -> None:
        """Show the files the selected mod shares with other mods, and whose copy wins."""
        if mod := self.current_mod():
            self.show_conflicts(mod)

    def show_conflicts(self, mod: Mod | None = None) -> None:
        """The conflict report: for one mod, or for the whole library."""
        try:
            plan = deploy.build_plan(self.library, self.foreign)
        except (pathops.PathOpError, MetadataError, OSError) as exc:
            self.error(exc)
            return
        if report.has_conflicts(self.library, plan, mod.id if mod else None):
            self.push_screen(
                ConflictsScreen(self.library, mod, self.foreign), lambda _: self.refresh_table()
            )
        elif mod:
            self.notify(f"{mod.name} has no conflicts", markup=False)
        else:
            self.notify("No conflicts")

    def action_apply(self) -> None:
        user_dir = self.config.game_user_dir
        if not user_dir:
            self.error("Game user dir not found: set game_user_dir in config.toml")
            return
        self.refresh_foreign()
        try:
            plan = deploy.build_plan(self.library, self.foreign)
        except (pathops.PathOpError, MetadataError, OSError) as exc:
            self.error(exc)
            return
        changes = deploy.compare(plan, self._record(), self.foreign)
        lines = [
            f"Apply to\n{user_dir} ?\n\n{changes.write} file(s) to write, {changes.remove} to"
            f" remove, {changes.unchanged} unchanged"
        ]
        lines += [f"\nwarning: {warning}" for warning in plan.warnings]
        lines += [
            f"\n{target}\n  merged from: {', '.join(mods)}" for target, mods in plan.merged.items()
        ]
        if plan.addins:
            lines.append(f"\nAddIns: {', '.join(addin.name for addin in plan.addins)}")
        errors, open_ = plan.with_status(deploy.ERROR), plan.with_status(deploy.OPEN)
        if errors:
            lines.append(f"\n{len(errors)} overwrite(s) in the wrong load order:")
            lines += [f"  {report.file_line(self.library, conflict)}" for conflict in errors[:8]]
        if open_:
            lines.append(f"\n{len(open_)} conflicting file(s), last mod wins:")
            lines += [f"  {c.name}: {' < '.join(c.mod_ids)}" for c in open_[:8]]
        if len(errors) > 8 or len(open_) > 8:
            lines.append("  ... (v shows the full report)")
        if accepted := len(plan.with_status(deploy.ACCEPTED)):
            lines.append(f"\n{accepted} accepted overwrite(s)")

        def done(confirmed: bool | None) -> None:
            if confirmed:
                self.run_apply(plan, user_dir)

        self.push_screen(ConfirmScreen("\n".join(lines)), done)

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool:
        """No key does anything while a long operation runs: not even quit.

        And while the filter is being typed, keys are text, not commands.
        """
        if self.focused is not None and self.focused.id == "filter":
            return action == "clear_filter"
        return not self._busy

    async def run_with_progress(
        self, title: str, operation: Callable[..., T], *args: object, splash: bool = False
    ) -> T:
        """Run a blocking operation in a thread behind a progress dialog.

        `operation` is called with `args` plus a progress callback as its last argument.
        With `splash`, the dialog shows the program's banner.
        """
        screen = ProgressScreen(title, splash=splash)
        self._busy = True
        await self.push_screen(screen)
        last, shown = 0.0, ""

        def progress(phase: str, done: int, total: int) -> None:
            nonlocal last, shown
            now = time.monotonic()
            # do not flood the UI with updates, but never skip the start or end of a phase
            if phase != shown or done >= total or now - last >= 0.05:
                last, shown = now, phase
                self.call_from_thread(screen.advance, phase, done, total)

        try:
            return await asyncio.to_thread(operation, *args, progress)
        finally:
            self._busy = False
            await screen.dismiss()

    @work
    async def run_apply(self, plan: deploy.Plan, user_dir: Path) -> None:
        deployed_file = self.config.dirs.deployed_file
        install_dir = self.config.game_install_dir

        def apply(
            progress: deploy.Progress,
        ) -> tuple[deploy.ApplyResult, str | None, textures.Summary]:
            note = itemlists.fill(plan, install_dir, user_dir, deployed_file, progress)
            reduced = textures.limit(plan, self.config.max_texture_size, progress)
            return deploy.apply(plan, user_dir, deployed_file, progress), note, reduced

        try:
            result, note, reduced = await self.run_with_progress("Applying mods", apply)
        except (deploy.DeployError, OSError, ValueError) as exc:
            self.error(exc)
            return
        finally:
            self.refresh_status()
        revisions.snapshot(self.config.dirs.data, f"apply: {result.written} files")
        message = (
            f"Applied: wrote {result.written} file(s), removed {result.removed},"
            f" {result.unchanged} unchanged"
        )
        if result.kept:
            message += f", left {len(result.kept)} file(s) changed by hand alone"
        if result.unregistered:
            message += f", unregistered {', '.join(result.unregistered)}"
        self.refresh_foreign()
        self.refresh_table()
        if result.skipped:
            names = ", ".join(path.rpartition("/")[2] for path in result.skipped[:4])
            more = ", …" if len(result.skipped) > 4 else ""
            message += (
                f", left {len(result.skipped)} existing file(s) untouched: {names}{more}"
                " (t → Adopt takes them over)"
            )
        self.notify(message, markup=False)
        for text in (note, reduced.note()):
            if text:
                self.notify(text, markup=False)
        if warning := reduced.warning():
            self.notify(warning, severity="warning", timeout=10, markup=False)

    def action_unapply(self) -> None:
        self.start_unapply()

    @staticmethod
    def _foreign_message(foreign: deploy.Foreign) -> str:
        lines = ["The mod directories contain things that dao-modman does not manage:", ""]
        if foreign.unmanaged:
            lines.append(f"  {len(foreign.unmanaged)} file(s) it has no record of, for example")
            lines += [f"    {path}" for path in foreign.unmanaged[:4]]
        if foreign.modified:
            lines.append(f"  {len(foreign.modified)} applied file(s) that were changed since")
            lines += [f"    {path}" for path in foreign.modified[:4]]
        if foreign.addins:
            lines.append(f"  AddIns registered by something else: {', '.join(foreign.addins)}")
        lines += ["", "A plain unapply leaves them where they are."]
        return "\n".join(lines)

    @work
    async def start_unapply(self) -> None:
        """Unapply, first asking what to do about anything else in the mod directories."""
        applied = self.config.dirs.deployed_file.exists()
        user_dir = self.config.game_user_dir
        foreign = deploy.Foreign()
        if user_dir and user_dir.is_dir():
            try:
                foreign = await self.run_with_progress(
                    "Checking the mod directories",
                    deploy.scan,
                    user_dir,
                    self.config.dirs.deployed_file,
                    self.config.game_install_dir,
                )
            except (deploy.DeployError, addins.AddInsError, OSError, ValueError) as exc:
                self.error(exc)
                return
        if not foreign:
            if not applied:
                self.notify("Nothing is applied")
            elif await self.push_screen_wait(
                ConfirmScreen("Remove all applied mod files from the game?")
            ):
                await self.unapply(clean=False)
            return
        choice = await self.push_screen_wait(UnapplyScreen(self._foreign_message(foreign)))
        if choice == "all":
            await self.unapply(clean=True)
        elif choice == "ours" and applied:
            await self.unapply(clean=False)
        elif choice == "ours":
            self.notify("Nothing is applied")

    async def unapply(self, *, clean: bool) -> None:
        """Remove our files; with `clean`, everything else in the mod directories too."""
        deployed_file = self.config.dirs.deployed_file
        try:
            result = await self.run_with_progress(
                "Removing applied mods", deploy.unapply, deployed_file
            )
            cleaned = 0
            if clean and self.config.game_user_dir:
                cleaned = await asyncio.to_thread(
                    deploy.clean,
                    self.config.game_user_dir,
                    deployed_file,
                    self.config.game_install_dir,
                )
        except (deploy.DeployError, addins.AddInsError, OSError, ValueError) as exc:
            self.error(exc)
            return
        finally:
            self.refresh_foreign()
            self.refresh_table()
        revisions.snapshot(self.config.dirs.data, f"unapply{' and clean' if clean else ''}")
        message = f"Removed {result.removed} applied file(s)"
        if clean:
            message += f" and {cleaned} that dao-modman did not manage"
        elif result.kept:
            message += f", kept {len(result.kept)} modified file(s)"
        self.notify(message)

    def action_keys(self) -> None:
        """Show every key; the footer only lists the everyday ones."""
        self.push_screen(KeysScreen(key_overview()))

    def action_launch(self) -> None:
        """Start the game through Steam."""
        try:
            launch.launch(self.config.game_app_id)
        except launch.LaunchError as exc:
            self.error(exc)
            return
        enabled = any(entry.enabled for entry in self.library.profile.mods)
        if enabled and not self.config.dirs.deployed_file.exists():
            self.notify("Launching the game - note: your mods are not applied", severity="warning")
        else:
            self.notify("Launching the game through Steam")

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
        limit = self.config.max_texture_size
        current = textures.LIMITS.get(limit, f"{limit} px")
        tools.append(("texture_size", f"Limit the size of textures - currently {current}"))
        tools.append(("conflicts", "Show all conflicts"))
        tools.append(("adopt", "Adopt files not managed by dao-modman"))
        try:
            console_on = bool(install_dir) and console.is_enabled(install_dir)
        except console.ConsoleError:
            console_on = False
        if console_on:
            tools.append(("console_disable", "Disable the developer console"))
        else:
            tools.append(("console_enable", "Enable the developer console"))

        tools.append(("chargen", "Check the character creator list (chargenmorphcfg.xml)"))

        def done(tool: str | None) -> None:
            if tool == "chargen":
                self.show_chargen_report()
            elif tool == "texture_size":
                self.choose_texture_size()
            elif tool == "conflicts":
                self.show_conflicts()
            elif tool == "adopt":
                self.start_adopt()
            elif tool == "laa_patch":
                self.patch_laa()
            elif tool:
                self.run_tool(tool)

        self.push_screen(ChoiceScreen("Tools", tools), done)

    def refresh_foreign(self) -> None:
        """Look for files in the mod directories that dao-modman does not manage."""
        user_dir = self.config.game_user_dir
        if not user_dir or not user_dir.is_dir():
            self.foreign = []
            return
        try:
            self.foreign = deploy.unmanaged(
                user_dir, self.config.dirs.deployed_file, self.config.game_install_dir
            )
        except (deploy.DeployError, addins.AddInsError, OSError) as exc:
            self.foreign = []
            self.error(exc)

    @work(exclusive=True, group="adopt")
    async def start_adopt(self) -> None:
        """Take the files we do not manage into the library, then offer to apply."""
        user_dir = self.config.game_user_dir
        if not user_dir or not user_dir.is_dir():
            self.error("Game user dir not found: set game_user_dir in config.toml")
            return
        try:
            found = adopt.find(
                self.library, user_dir, self.config.dirs.deployed_file, self.config.game_install_dir
            )
        except (deploy.DeployError, addins.AddInsError, OSError) as exc:
            self.error(exc)
            return
        if not found.groups:
            self.notify("Every file in the mod directories is managed by dao-modman")
            return
        if not await self.push_screen_wait(ConfirmScreen(adopt.describe(found) + "\n\nAdopt?")):
            return
        try:
            result = await self.run_with_progress(
                "Adopting", adopt.adopt, self.library, user_dir, found
            )
        except (adopt.AdoptError, addins.AddInsError, OSError) as exc:
            self.error(exc)
            return
        finally:
            self.refresh_foreign()
            self.refresh_table()
        self.notify(f"Adopted {len(result.mods)} mod(s), {result.removed} file(s)", markup=False)
        message = (
            "The adopted files are now in the library, and no longer in the game.\n\n"
            "Apply now to install them again?"
        )
        if await self.push_screen_wait(ConfirmScreen(message)):
            self.action_apply()

    def choose_texture_size(self) -> None:
        """Set the longest side a deployed texture may have; kept in config.toml."""

        def done(choice: str | None) -> None:
            if choice is None or int(choice) == self.config.max_texture_size:
                return
            if self.config.config_file is None:
                self.error("No config file to save the setting in")
                return
            try:
                set_option(self.config.config_file, "max_texture_size", int(choice))
            except (OSError, ValueError) as exc:
                self.error(exc)
                return
            self.config.max_texture_size = int(choice)
            label = textures.LIMITS[int(choice)]
            self.notify(f"Texture size limit: {label}. Apply to put it into effect.")

        choices = [(str(size), label) for size, label in textures.LIMITS.items()]
        title = "Largest texture to deploy (larger ones are reduced at apply)"
        selected = str(self.config.max_texture_size)
        self.push_screen(ChoiceScreen(title, choices, selected=selected), done)

    @work
    async def show_chargen_report(self) -> None:
        """Show what the merged character creator list holds, and which entries are broken."""

        def build(progress: deploy.Progress) -> str:
            return report.chargen_report(self.config.game_install_dir, self.library, progress)[1]

        try:
            text = await self.run_with_progress("Checking the character creator list", build)
        except (pathops.PathOpError, OSError, ValueError) as exc:
            self.error(exc)
            return
        self.push_screen(TextScreen("Character creator list", text))

    def patch_laa(self) -> None:
        """Open the guide to getting an LAA executable, then install the one the user names."""
        install_dir = self.config.game_install_dir
        if not install_dir:
            self.error("Game install dir not found: set game_install_dir in config.toml")
            return

        def done(path: str | None) -> None:
            if not path:
                return
            try:
                backup = laa.patch(install_dir, Path(path))
            except laa.LaaError as exc:
                self.error(exc)
                return
            self.notify(f"LAA patch applied, original saved as {backup.name}", markup=False)

        self.open_url(laa.GUIDE_URL)
        title = "Path of your LAA-patched DAOrigins.exe (the guide is open in your browser)"
        self.push_screen(TextPromptScreen(title, placeholder="/path/to/DAOrigins.exe"), done)

    def run_tool(self, tool: str) -> None:
        install_dir = self.config.game_install_dir
        if not install_dir:
            self.error("Game install dir not found: set game_install_dir in config.toml")
            return
        try:
            if tool == "laa_restore":
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
