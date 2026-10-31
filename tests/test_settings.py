from __future__ import annotations

from pathlib import Path

import httpx2
import pytest
from textual.widgets import Input, OptionList, TextArea

from dao_modman.core import config, nxm_handler
from dao_modman.core.config import Config
from dao_modman.core.paths import Dirs
from dao_modman.tui import settings
from dao_modman.tui.app import ModManApp
from dao_modman.tui.screens import ChoiceScreen, TextPromptScreen
from dao_modman.tui.settings import PatternsScreen, SettingsScreen, SetupWizard

from .test_tui import _started, until


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start like test_tui does; nothing is looked up on this machine."""
    monkeypatch.setattr(ModManApp, "run_test", _started)
    for name in ("find_game_user_dir", "find_game_install_dir", "find_game_app_id"):
        monkeypatch.setattr(config.paths, name, lambda *_: None)
    monkeypatch.setattr(nxm_handler, "status", lambda: (nxm_handler.NONE, ""))
    monkeypatch.delenv("NEXUS_API_TOKEN", raising=False)


@pytest.fixture
def setup(tmp_path: Path, dirs: Dirs, user_dir: Path) -> Config:
    """A config.toml naming the folders, and the Config read from it."""
    install = tmp_path / "install"
    (install / "bin_ship").mkdir(parents=True)
    (install / "bin_ship/DAOrigins.exe").write_bytes(b"MZ")
    file = tmp_path / "cfg/config.toml"
    file.parent.mkdir()
    file.write_text(
        "# keep me\n"
        f"game_user_dir = '{user_dir}'\ngame_install_dir = '{install}'\n"
        f"data_dir = '{dirs.data}'\ndownloads_dir = '{dirs.downloads}'\n"
    )
    return config.load_config(file)


def select(screen, key: str) -> None:
    options = screen.query_one("#settings", OptionList)
    ids = [options.get_option_at_index(i).id for i in range(options.option_count)]
    options.highlighted = ids.index(key)


async def test_settings_change_and_reset(setup: Config) -> None:
    app = ModManApp(setup)
    file = setup.config_file
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("s")
        screen = app.screen
        assert isinstance(screen, SettingsScreen)

        select(screen, "max_texture_size")  # a choice
        await pilot.press("enter")
        assert isinstance(app.screen, ChoiceScreen)
        choices = app.screen.query_one(OptionList)
        choices.highlighted = [key for key, _ in app.screen.choices].index("2048")
        await pilot.press("enter")
        await pilot.pause()
        assert app.config.max_texture_size == 2048
        assert "max_texture_size = 2048" in file.read_text()

        select(screen, "launch_command")  # a command, typed as one line
        await pilot.press("enter")
        assert isinstance(app.screen, TextPromptScreen)
        app.screen.query_one(Input).value = "lutris lutris:rungame/dao"
        await pilot.press("enter")
        await pilot.pause()
        assert app.config.launch_command == ["lutris", "lutris:rungame/dao"]

        select(screen, "game_install_dir")  # refused: not the game's folder
        await pilot.press("enter")
        app.screen.query_one(Input).value = str(file.parent)
        await pilot.press("enter")
        await pilot.pause()
        assert "not the game's install folder" in [n.message for n in app._notifications][-1]
        assert app.config.game_install_dir == setup.game_install_dir

        select(screen, "ignore")  # a list, one entry per line
        await pilot.press("enter")
        assert isinstance(app.screen, PatternsScreen)
        app.screen.query_one(TextArea).text = "*.txt\n\n/docs/\n"
        await pilot.press("ctrl+s")
        await pilot.pause()
        assert app.config.ignore == ["*.txt", "/docs/"]
        assert app.library.ignore.matches("docs/readme.erf")

        select(screen, "launch_command")
        await pilot.press("delete")  # back to the default: the line goes
        await pilot.pause()
        assert app.config.launch_command is None
        assert "launch_command" not in file.read_text()
        assert file.read_text().startswith("# keep me\n")

        labels = [str(o.prompt) for o in screen.query_one("#settings", OptionList).options]
        assert "Texture size limit: 2K (2048 px)" in labels
        assert "Language of item names: English (en-us)  (default)" in labels
        await pilot.press("escape")
        assert not isinstance(app.screen, SettingsScreen)


async def test_api_key_is_checked_before_it_is_saved(setup: Config) -> None:
    def handler(request: httpx2.Request) -> httpx2.Response:
        if request.headers.get("apikey") != "good":
            return httpx2.Response(401, json={"message": "Please provide a valid API Key"})
        return httpx2.Response(200, json={"name": "seb", "is_premium": False})

    app = ModManApp(setup, transport=httpx2.MockTransport(handler))
    token_file = setup.config_file.parent / ".nexus-token"
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.press("s")
        screen = app.screen
        for key in ("wrong", "good"):
            select(screen, settings.TOKEN_ROW)
            await pilot.press("enter")
            prompt = app.screen.query_one(Input)
            assert prompt.password  # the key is not shown while typing
            prompt.value = key
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            if key == "wrong":
                assert "did not accept" in [n.message for n in app._notifications][-1]
                assert not token_file.exists() and app.nexus is None
        assert token_file.read_text() == "good\n"
        assert app.nexus is not None and app.account == {"name": "seb", "is_premium": False}
        labels = [str(o.prompt) for o in screen.query_one("#settings", OptionList).options]
        assert "Nexus API key: set, account seb (free)" in labels


async def test_wizard_on_first_start(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    file = tmp_path / "cfg/config.toml"  # not there yet: the first start
    found = {"game_user_dir": user_dir, "game_install_dir": tmp_path}
    app = ModManApp(Config(dirs=dirs, config_file=file, **found))
    async with app.run_test(size=(140, 40)) as pilot:
        await until(pilot, lambda: isinstance(app.screen, SetupWizard))
        wizard = app.screen
        titles = []
        for _ in settings.STEPS:  # enter takes the highlighted way on: Next, Skip, Finish
            titles.append(str(wizard.query_one("#wizard-title").render()))
            await pilot.press("enter")
            await pilot.pause()
        assert titles[0] == "Setup (1/6): Welcome" and titles[-1] == "Setup (6/6): Done"
        assert not isinstance(app.screen, SetupWizard)
    assert file.read_text() == settings.CONFIG_HEADER

    again = ModManApp(Config(dirs=dirs, config_file=file, **found))
    async with again.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert not isinstance(again.screen, SetupWizard)  # only once


async def test_wizard_when_the_game_is_not_found(setup: Config) -> None:
    setup.game_install_dir = None
    app = ModManApp(setup)
    async with app.run_test(size=(140, 40)) as pilot:
        await until(pilot, lambda: isinstance(app.screen, SetupWizard))
        assert settings.STEPS[app.screen.step] == "game"
        text = str(app.screen.query_one("#wizard-text").render())
        assert "Install folder: not found" in text
        await pilot.press("left")  # back to the start
        assert settings.STEPS[app.screen.step] == "welcome"
        await pilot.press("escape")  # leave
        assert not isinstance(app.screen, SetupWizard)
