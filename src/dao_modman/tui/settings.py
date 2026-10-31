"""Settings screen and setup wizard: config.toml, the Nexus API key, the nxm:// handler.

Users should never need to edit config.toml by hand; everything it holds can be set here.
"""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from rich.markup import escape
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Label, OptionList, Static, TextArea
from textual.widgets.option_list import Option

from ..core import archive, config, console, laa, nxm_handler, paths
from ..core.config import TOKEN_FILE, TOKEN_VAR, Setting, Value
from .screens import ChoiceScreen, ConfirmScreen, TextPromptScreen

if TYPE_CHECKING:
    from .app import ModManApp

WINDOWS = sys.platform == "win32"
API_KEYS_URL = "https://www.nexusmods.com/settings/api-keys"
SETTINGS = {setting.key: setting for setting in config.SETTINGS}
CONFIG_HEADER = "# dao-modman settings: change them in dao-modman (key s), or here\n"

TOKEN_ROW, HANDLER_ROW, WIZARD_ROW = "_token", "_handler", "_wizard"
EXTRA_HELP = {
    TOKEN_ROW: "Your personal API key from nexusmods.com: downloads, update checks.",
    HANDLER_ROW: "Makes the 'Mod Manager Download' button on Nexus hand downloads to dao-modman.",
    WIZARD_ROW: "Game folders, Nexus API key, nxm:// links and the game's setup, step by step.",
}


# -- reading and writing settings ----------------------------------------------


def current(app: ModManApp, setting: Setting) -> Value | None:
    """The value in effect: from config.toml, found automatically, or the default."""
    cfg = app.config
    values: dict[str, Value | None] = {
        "game_install_dir": str(cfg.game_install_dir) if cfg.game_install_dir else None,
        "game_user_dir": str(cfg.game_user_dir) if cfg.game_user_dir else None,
        "launch_command": cfg.launch_command,
        "game_app_id": cfg.game_app_id,
        "max_texture_size": cfg.max_texture_size,
        "language": cfg.language,
        "ignore": cfg.ignore,
        "unrar_command": cfg.unrar_command,
        "terminal": cfg.terminal,
        "data_dir": str(cfg.dirs.data),
        "downloads_dir": str(cfg.dirs.downloads),
    }
    return values[setting.key]


def join_command(argv: list[str]) -> str:
    return subprocess.list2cmdline(argv) if WINDOWS else shlex.join(argv)


def split_command(text: str) -> list[str]:
    """A command line as typed, split into arguments; ValueError on unbalanced quotes."""
    if not WINDOWS:
        return shlex.split(text)
    return [
        part[1:-1] if part[:1] == part[-1:] == '"' else part
        for part in shlex.split(text, posix=False)
    ]


def describe(app: ModManApp, setting: Setting, raw: dict) -> str:
    """`Label: value`, marked when config.toml does not set it."""
    value = current(app, setting)
    if setting.kind == config.KIND_CHOICE:
        text = dict(setting.choices).get(value, str(value))  # type: ignore[arg-type]
    elif setting.kind == config.KIND_COMMAND:
        text = join_command(value) if isinstance(value, list) else "-"
    elif setting.kind == config.KIND_PATTERNS:
        text = f"{len(value or [])} pattern(s)"
    else:
        text = str(value) if value else "-"
    if setting.key in raw:
        return f"{setting.label}: {text}"
    if setting.kind in (config.KIND_INSTALL_DIR, config.KIND_USER_DIR):
        return (
            f"{setting.label}: {text}  (found automatically)"
            if value
            else f"{setting.label}: not found"
        )
    return f"{setting.label}: {text}  (default)"


def store(app: ModManApp, setting: Setting, value: Value | None) -> bool:
    """Check and save a value (None: back to the default); the app takes it over at once."""
    if value is not None and (problem := config.validate(setting, value)):
        app.error(problem)
        return False
    if app.config.config_file is None:
        app.error("No config file to save the settings in")
        return False
    try:
        config.set_option(app.config.config_file, setting.key, value)
    except (OSError, ValueError) as exc:
        app.error(exc)
        return False
    app.config_changed()
    return True


def edit_setting(app: ModManApp, setting: Setting, done: Callable[[], None]) -> None:
    """Ask for a new value in the dialog that suits the setting, then save it."""
    value = current(app, setting)
    title = f"{setting.label}: {setting.help}"

    if setting.kind == config.KIND_CHOICE:

        def chosen(key: str | None) -> None:
            if key is not None:
                store(app, setting, next(v for v, _ in setting.choices if str(v) == key))
            done()

        choices = [(str(choice), label) for choice, label in setting.choices]
        app.push_screen(ChoiceScreen(title, choices, selected=str(value)), chosen)
    elif setting.kind == config.KIND_PATTERNS:

        def edited(patterns: list[str] | None) -> None:
            if patterns is not None:
                store(app, setting, patterns)
            done()

        app.push_screen(PatternsScreen(title, value if isinstance(value, list) else []), edited)
    else:

        def entered(text: str | None) -> None:
            if text is not None:
                if setting.kind == config.KIND_COMMAND and text:
                    try:
                        store(app, setting, split_command(text))
                    except ValueError as exc:
                        app.error(f"{setting.label}: {exc}")
                else:
                    store(app, setting, text or None)  # empty: back to the default
            done()

        shown = join_command(value) if isinstance(value, list) else str(value or "")
        hint = "empty: found automatically" if "dir" in setting.kind else "empty: the default"
        app.push_screen(TextPromptScreen(f"{title} ({hint})", shown), entered)


def config_dir(app: ModManApp) -> Path:
    return app.config.config_file.parent if app.config.config_file else paths.config_dir()


def token_line(app: ModManApp) -> str:
    if os.environ.get(TOKEN_VAR, "").strip():
        return f"Nexus API key: from {TOKEN_VAR}"
    if not app.config.nexus_token:
        return "Nexus API key: not set"
    if app.account:
        kind = "premium" if app.account.get("is_premium") else "free"
        return f"Nexus API key: set, account {app.account.get('name', '?')} ({kind})"
    return "Nexus API key: set"


def enter_token(app: ModManApp, done: Callable[[], None]) -> None:
    """Ask for the API key; the app checks it with Nexus before it is saved."""

    def entered(token: str | None) -> None:
        if token:
            app.use_token(token, done)
        else:
            done()

    title = f"Nexus API key (from {API_KEYS_URL}); saved to {config_dir(app) / TOKEN_FILE}"
    app.push_screen(TextPromptScreen(title, placeholder="paste the key", password=True), entered)


def handler_line() -> str:
    state, other = nxm_handler.status()
    if state == nxm_handler.OURS:
        return "nxm:// links: handled by dao-modman"
    if state == nxm_handler.OTHER:
        return f"nxm:// links: handled by another program ({other})"
    return "nxm:// links: not registered"


def toggle_handler(app: ModManApp, done: Callable[[], None]) -> None:
    """Register dao-modman for nxm:// links, or unregister it, after asking."""
    state, other = nxm_handler.status()
    if state == nxm_handler.OURS:
        question, action = "Stop handling nxm:// links?", nxm_handler.uninstall
    else:
        question = "Register dao-modman for 'Mod Manager Download' links (nxm://)?"
        if other:
            question += f"\n\nThis replaces the current handler: {other}"
        action = nxm_handler.install

    def answer(yes: bool | None) -> None:
        if yes:
            try:
                app.notify("\n".join(action()), markup=False)
            except nxm_handler.HandlerError as exc:
                app.error(exc)
        done()

    app.push_screen(ConfirmScreen(question), answer)


def ensure_config_file(app: ModManApp) -> None:
    """After the first setup there is a config.toml, even if nothing in it differs."""
    file = app.config.config_file
    if file is None or file.exists():
        return
    try:
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(CONFIG_HEADER, encoding="utf-8")
    except OSError as exc:
        app.error(exc)


# -- screens --------------------------------------------------------------------


class PatternsScreen(ModalScreen[list[str] | None]):
    """Edit a list, one entry per line. Result: the entries, or None if cancelled."""

    BINDINGS = [Binding("ctrl+s", "save", "Save"), Binding("escape", "cancel", "Cancel")]

    def __init__(self, title: str, entries: list[str]) -> None:
        super().__init__()
        self.title_text = title
        self.entries = entries

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label(self.title_text, classes="title", markup=False)
            yield TextArea("\n".join(self.entries), id="patterns")
            yield Label("one per line   [b]ctrl+s[/] save   [b]esc[/] cancel", classes="hint")

    def action_save(self) -> None:
        text = self.query_one(TextArea).text
        self.dismiss([line.strip() for line in text.splitlines() if line.strip()])

    def action_cancel(self) -> None:
        self.dismiss(None)


class SettingsScreen(ModalScreen[None]):
    """Every setting with its value; enter changes one, delete puts it back to the default."""

    BINDINGS = [
        Binding("escape,q,s", "close", "Close"),
        Binding("delete", "reset", "Back to default"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("Settings", classes="title")
            yield OptionList(id="settings")
            yield Static("", id="setting-help", markup=False)
            yield Label(
                "[b]enter[/] change   [b]del[/] back to default   [b]esc[/] close", classes="hint"
            )

    def on_mount(self) -> None:
        self.rebuild()

    @property
    def options(self) -> OptionList:
        return self.query_one("#settings", OptionList)

    def rebuild(self) -> None:
        app: ModManApp = self.app  # type: ignore[assignment]
        raw = config.load_raw(app.config.config_file)
        rows = [(setting.key, describe(app, setting, raw)) for setting in config.SETTINGS]
        rows += [
            (TOKEN_ROW, token_line(app)),
            (HANDLER_ROW, handler_line()),
            (WIZARD_ROW, "Run the setup wizard"),
        ]
        highlighted = self.options.highlighted or 0
        self.options.clear_options()
        self.options.add_options([Option(escape(label), id=key) for key, label in rows])
        self.options.highlighted = min(highlighted, len(rows) - 1)

    def selected_id(self) -> str | None:
        index = self.options.highlighted
        return None if index is None else self.options.get_option_at_index(index).id

    @on(OptionList.OptionHighlighted, "#settings")
    def show_help(self, event: OptionList.OptionHighlighted) -> None:
        key = event.option.id or ""
        text = SETTINGS[key].help if key in SETTINGS else EXTRA_HELP.get(key, "")
        self.query_one("#setting-help", Static).update(text)

    @on(OptionList.OptionSelected, "#settings")
    def change(self, event: OptionList.OptionSelected) -> None:
        app: ModManApp = self.app  # type: ignore[assignment]
        key = event.option.id or ""
        if key in SETTINGS:
            edit_setting(app, SETTINGS[key], self.rebuild)
        elif key == TOKEN_ROW:
            enter_token(app, self.rebuild)
        elif key == HANDLER_ROW:
            toggle_handler(app, self.rebuild)
        elif key == WIZARD_ROW:
            app.push_screen(SetupWizard(), lambda _: self.rebuild())

    def action_reset(self) -> None:
        key = self.selected_id()
        if key in SETTINGS:
            store(self.app, SETTINGS[key], None)  # type: ignore[arg-type]
            self.rebuild()

    def action_close(self) -> None:
        self.dismiss(None)


STEPS = ("welcome", "game", "nexus", "handler", "checks", "done")
STEP_TITLES = {
    "welcome": "Welcome",
    "game": "The game's folders",
    "nexus": "Nexus Mods",
    "handler": "Downloads from the browser",
    "checks": "The game's setup",
    "done": "Done",
}


class SetupWizard(ModalScreen[None]):
    """The first setup, step by step; also opened when the game's folders are not found."""

    BINDINGS = [
        Binding("escape", "leave", "Leave"),
        Binding("left,b", "back", "Back"),
    ]

    def __init__(self, start: str = "welcome") -> None:
        super().__init__()
        self.step = STEPS.index(start)

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog wide"):
            yield Label("", id="wizard-title", classes="title")
            yield Static("", id="wizard-text", markup=False)
            yield OptionList(id="wizard-actions")
            yield Label(
                "[b]enter[/] choose   [b]←[/] back   [b]esc[/] leave the setup", classes="hint"
            )

    def on_mount(self) -> None:
        self.show()

    @property
    def name_of_step(self) -> str:
        return STEPS[self.step]

    def show(self) -> None:
        text, actions = getattr(self, f"_{self.name_of_step}")()
        title = f"Setup ({self.step + 1}/{len(STEPS)}): {STEP_TITLES[self.name_of_step]}"
        self.query_one("#wizard-title", Label).update(title)
        self.query_one("#wizard-text", Static).update(text)
        options = self.query_one("#wizard-actions", OptionList)
        options.clear_options()
        options.add_options([Option(escape(label), id=key) for key, label in actions])
        options.highlighted = len(actions) - 1  # going on is the usual choice
        options.focus()

    # what each step shows: (text, [(action id, label)])

    def _welcome(self) -> tuple[str, list[tuple[str, str]]]:
        text = (
            "dao-modman keeps your mods in a library of its own and applies the ones you enable "
            "to the game.\n\nThis setup finds the game, connects to Nexus Mods and checks the "
            "game's own setup. Everything can be changed later in the settings (key s)."
        )
        return text, [("next", "Start")]

    def _game(self) -> tuple[str, list[tuple[str, str]]]:
        app: ModManApp = self.app  # type: ignore[assignment]
        cfg = app.config
        lines = [
            f"Install folder: {cfg.game_install_dir or 'not found'}",
            f"User folder:    {cfg.game_user_dir or 'not found'}",
            "",
        ]
        if not cfg.game_install_dir:
            lines.append(
                "Not from Steam? Enter the folder that holds bin_ship"
                + ("\\" if WINDOWS else "/")
                + "DAOrigins.exe."
            )
        if not cfg.game_user_dir:
            lines.append(
                "The user folder (Documents/BioWare/Dragon Age) is made by the game when it first "
                "starts: start the game once, then come back here."
            )
        if cfg.game_install_dir and cfg.game_user_dir:
            lines.append("Both were found. Change them only if they are the wrong ones.")
        actions = [
            ("install_dir", "Change the install folder"),
            ("user_dir", "Change the user folder"),
            ("next", "Next"),
        ]
        return "\n".join(lines), actions

    def _nexus(self) -> tuple[str, list[tuple[str, str]]]:
        app: ModManApp = self.app  # type: ignore[assignment]
        text = (
            f"{token_line(app)}\n\ndao-modman downloads mods with your personal API key. Open "
            "the page, copy the key under 'Personal API Key' (create one if there is none) and "
            f"enter it here. It is kept in {config_dir(app) / TOKEN_FILE}."
        )
        actions = [
            ("api_page", "Open the API key page in the browser"),
            ("api_key", "Enter the API key"),
            ("next", "Next" if app.config.nexus_token else "Skip"),
        ]
        return text, actions

    def _handler(self) -> tuple[str, list[tuple[str, str]]]:
        state, _other = nxm_handler.status()
        text = (
            f"{handler_line()}\n\nWith dao-modman as handler, the 'Mod Manager Download' button "
            "on Nexus hands the download to dao-modman, even when it is not running."
        )
        label = "Stop handling them" if state == nxm_handler.OURS else "Register dao-modman"
        return text, [("handler", label), ("next", "Next")]

    def _checks(self) -> tuple[str, list[tuple[str, str]]]:
        app: ModManApp = self.app  # type: ignore[assignment]
        install_dir = app.config.game_install_dir
        lines = []
        try:
            state = laa.status(install_dir) if install_dir else "unknown (install folder not found)"
        except laa.LaaError as exc:
            state = str(exc)
        lines.append(f"LAA patch: {state}")
        if state == laa.UNPATCHED:
            lines.append(
                "  Without it the game can only use 2 GB of memory, and heavily modded games "
                "crash. Tools (t) -> LAA patch."
            )
        try:
            enabled = install_dir is not None and console.is_enabled(install_dir)
            lines.append(f"Developer console: {'enabled' if enabled else 'disabled'}")
        except console.ConsoleError:
            lines.append("Developer console: unknown")
        tools = [name for name in ("unrar", "7z", "bsdtar") if archive.find_tool(name)]
        if tools or app.config.unrar_command:
            lines.append(f".rar archives: {', '.join(tools) or 'unrar_command'}")
        else:
            lines.append(".rar archives: no program found")
            lines.append(
                "  Some mods are .rar archives: install "
                + ("WinRAR or 7-Zip." if WINDOWS else "unrar or 7-Zip (7z).")
            )
        return "\n".join(lines), [("next", "Next")]

    def _done(self) -> tuple[str, list[tuple[str, str]]]:
        app: ModManApp = self.app  # type: ignore[assignment]
        missing = []
        if not (app.config.game_install_dir and app.config.game_user_dir):
            missing.append("the game's folders")
        if not app.config.nexus_token:
            missing.append("the Nexus API key (downloads need it)")
        text = "All set." if not missing else "Still missing: " + "; ".join(missing) + "."
        text += "\n\nThe settings (key s) change all of this later, and run this setup again."
        return text, [("finish", "Finish")]

    @on(OptionList.OptionSelected, "#wizard-actions")
    def choose(self, event: OptionList.OptionSelected) -> None:
        app: ModManApp = self.app  # type: ignore[assignment]
        action = event.option.id
        if action == "next":
            self.step += 1
            self.show()
        elif action == "finish":
            self.action_leave()
        elif action == "install_dir":
            edit_setting(app, SETTINGS["game_install_dir"], self.show)
        elif action == "user_dir":
            edit_setting(app, SETTINGS["game_user_dir"], self.show)
        elif action == "api_page":
            app.open_url(API_KEYS_URL)
        elif action == "api_key":
            enter_token(app, self.show)
        elif action == "handler":
            toggle_handler(app, self.show)

    def action_back(self) -> None:
        if self.step > 0:
            self.step -= 1
            self.show()

    def action_leave(self) -> None:
        ensure_config_file(self.app)  # type: ignore[arg-type]
        self.dismiss(None)
