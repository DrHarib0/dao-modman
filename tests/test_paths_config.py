from __future__ import annotations

import os
from pathlib import Path

import pytest

from dao_modman.core import config, paths


def test_find_game_user_dir_in_extra_library(tmp_path: Path) -> None:
    root = tmp_path / "Steam"
    library = tmp_path / "games"
    (root / "steamapps").mkdir(parents=True)
    user_dir = library / "steamapps/compatdata/47810" / paths.USER_DIR_IN_PREFIX
    user_dir.mkdir(parents=True)
    (root / "steamapps/libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n "0" {{ "path" "{root}" }}\n "1" {{ "path" "{library}" }}\n}}\n'
    )
    assert paths.find_game_user_dir([root]) == user_dir


def test_find_game_user_dir_missing(tmp_path: Path) -> None:
    assert paths.find_game_user_dir([tmp_path]) is None


def test_steam_found_through_the_registry(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    steam = tmp_path / "Steam"
    monkeypatch.setattr(paths.windows, "steam_folders", lambda: [steam, tmp_path / "missing"])
    assert paths.steam_roots() == []
    steam.mkdir()
    assert paths.steam_roots() == [steam.resolve()]


def test_game_user_dir_in_windows_documents(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(paths, "steam_roots", lambda: [])
    monkeypatch.setattr(paths.windows, "documents_dir", lambda: tmp_path / "OneDrive/Documents")
    assert paths.find_game_user_dir() is None  # the game has not been started yet
    user_dir = tmp_path / "OneDrive/Documents/BioWare/Dragon Age"
    user_dir.mkdir(parents=True)
    assert paths.find_game_user_dir() == user_dir
    assert paths.find_game_user_dir([tmp_path]) is None  # given Steam roots only


@pytest.mark.skipif(os.name != "nt", reason="asks Windows")
def test_windows_lookups_do_not_fail() -> None:
    documents = paths.windows.documents_dir()
    assert documents is not None and documents.is_dir()
    assert all(isinstance(folder, Path) for folder in paths.windows.steam_folders())


def test_windows_lookups_find_nothing_elsewhere(monkeypatch: pytest.MonkeyPatch) -> None:
    if os.name == "nt":
        pytest.skip("only off Windows")
    assert paths.windows.documents_dir() is None and paths.windows.steam_folders() == []
    monkeypatch.setenv("APPDATA", "/nonsense")  # set or not, it means nothing here
    assert paths.windows.app_data() is None and paths.windows.local_app_data() is None


def test_config_and_data_dirs_on_windows(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.setattr(paths.windows, "app_data", lambda: tmp_path / "Roaming")
    monkeypatch.setattr(paths.windows, "local_app_data", lambda: tmp_path / "Local")
    assert paths.config_dir() == tmp_path / "Roaming" / paths.APP_NAME
    assert paths.data_dir() == tmp_path / "Local" / paths.APP_NAME
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))  # still wins, as on Linux
    assert paths.config_dir() == tmp_path / "xdg" / paths.APP_NAME


@pytest.mark.skipif(os.name != "nt", reason="asks Windows")
def test_windows_app_data_folders() -> None:
    assert paths.windows.app_data() == Path(os.environ["APPDATA"])
    assert paths.windows.local_app_data() == Path(os.environ["LOCALAPPDATA"])


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("a/b", "a/b"), ("/a//b/", "a/b"), ("a\\b", "a/b"), ("./a", "a")],
)
def test_normalize_rel(raw: str, expected: str) -> None:
    assert paths.normalize_rel(raw) == expected


@pytest.mark.parametrize("raw", ["", "/", "../a", "a/../b", "C:/x"])
def test_normalize_rel_rejects(raw: str) -> None:
    with pytest.raises(ValueError):
        paths.normalize_rel(raw)


def test_resolve_case_reuses_existing_casing(tmp_path: Path) -> None:
    (tmp_path / "Packages/Core").mkdir(parents=True)
    resolved = paths.resolve_case(tmp_path, "packages/core/override/x.erf")
    assert resolved == tmp_path / "Packages/Core/override/x.erf"


def test_token_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv(config.TOKEN_VAR, raising=False)
    (tmp_path / config.TOKEN_FILE).write_text("from-cwd\n")  # never read from the cwd
    (tmp_path / ".env").write_text(f"{config.TOKEN_VAR}=from-env-file\n")  # nor from .env
    assert config.load_token(config_dir) is None

    token_file = config_dir / config.TOKEN_FILE
    token_file.write_text("  \n")
    assert config.load_token(config_dir) is None  # empty
    token_file.write_text("from-file\n")
    assert config.load_token(config_dir) == "from-file"
    monkeypatch.setenv(config.TOKEN_VAR, " from-env ")
    assert config.load_token(config_dir) == "from-env"
    assert config.load_token(config_dir, environment=False) == "from-file"


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_token_file_readable_by_others_is_reported(tmp_path: Path) -> None:
    token_file = tmp_path / config.TOKEN_FILE
    assert config.token_warning(tmp_path) is None  # no file
    token_file.write_text("secret")
    token_file.chmod(0o644)
    warning = config.token_warning(tmp_path)
    assert warning is not None and "chmod 600" in warning and "secret" not in warning
    token_file.chmod(0o600)
    assert config.token_warning(tmp_path) is None


def test_load_config_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv(config.TOKEN_VAR, "tok")
    config_dir = tmp_path / "cfg" / paths.APP_NAME
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        f"game_user_dir = '{tmp_path}/game'\ndownloads_dir = '{tmp_path}/dl'\n"
    )
    cfg = config.load_config()
    assert cfg.game_user_dir == tmp_path / "game"
    assert cfg.dirs.downloads == tmp_path / "dl"
    assert cfg.dirs.data == tmp_path / "share" / paths.APP_NAME
    assert cfg.nexus_token == "tok"
    assert cfg.language == "en-us"
    assert cfg.max_texture_size == 4096
    assert "*.txt" in cfg.ignore and "*readme*" in cfg.ignore  # built-in list by default

    with (config_dir / "config.toml").open("a") as fh:
        fh.write('ignore = ["*.txt", "/docs/"]\n')
    assert config.load_config().ignore == ["*.txt", "/docs/"]


def test_launch_command_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    config_file = tmp_path / "cfg" / paths.APP_NAME / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("")
    assert config.load_config().launch_command is None

    config_file.write_text('launch_command = ["lutris", "lutris:rungame/dragon-age-origins"]\n')
    assert config.load_config().launch_command == ["lutris", "lutris:rungame/dragon-age-origins"]


def test_unrar_command_from_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    config_file = tmp_path / "cfg" / paths.APP_NAME / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text("")
    assert config.load_config().unrar_command is None

    config_file.write_text("unrar_command = ['C:\\Tools\\unrar.exe', 'x', '{archive}']\n")
    assert config.load_config().unrar_command == ["C:\\Tools\\unrar.exe", "x", "{archive}"]


@pytest.mark.parametrize(
    "text",
    [
        'launch_command = "lutris"',
        "launch_command = []",
        'launch_command = ["a", 1]',
        'unrar_command = "unrar"',
        'unrar_command = ["unrar", "x", "{dest}"]',  # no {archive}
        "x = [",
    ],
)
def test_bad_config_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], text: str
) -> None:
    from dao_modman.__main__ import main

    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    config_file = tmp_path / "cfg" / paths.APP_NAME / "config.toml"
    config_file.parent.mkdir(parents=True)
    config_file.write_text(text + "\n")
    with pytest.raises(config.ConfigError):
        config.load_config()
    assert main(["conflicts"]) == 1
    assert capsys.readouterr().err.startswith(f"error: {config_file}")


def test_set_option_keeps_the_rest_of_the_file(tmp_path: Path) -> None:
    file = tmp_path / "new" / "config.toml"
    config.set_option(file, "max_texture_size", 2048)  # no file yet
    assert file.read_text() == "max_texture_size = 2048\n"

    text = (
        'terminal = "kitty"\n\n# what is never deployed\nignore = [\n    "*.txt",  # text\n]\n'
        "max_texture_size=2048\n\n[table]\nmax_texture_size = 1\n"
    )
    file.write_text(text)
    config.set_option(file, "max_texture_size", 0)
    assert file.read_text() == text.replace("max_texture_size=2048", "max_texture_size = 0")

    file.write_text('# mine\nterminal = "kitty"\n\n[table]\nkey = 1\n')
    config.set_option(file, "max_texture_size", 1024)  # added above the first table
    assert file.read_text() == (
        '# mine\nterminal = "kitty"\n\nmax_texture_size = 1024\n[table]\nkey = 1\n'
    )

    file.write_text('note = """\nmax_texture_size = 5\n"""\n')
    with pytest.raises(ValueError, match="by hand"):
        config.set_option(file, "max_texture_size", 4096)
    assert "max_texture_size = 5" in file.read_text()  # left untouched


def test_set_option_writes_text_and_lists_and_removes_keys(tmp_path: Path) -> None:
    file = tmp_path / "config.toml"
    file.write_text(
        '# mine\nignore = [\n    "*.txt",  # text\n    "*.jpg",\n]\nlanguage = "de-de"\n'
    )

    config.set_option(file, "game_install_dir", r"C:\GOG Games\Dragon Age")
    assert "game_install_dir = 'C:\\GOG Games\\Dragon Age'\n" in file.read_text()
    config.set_option(file, "terminal", 'it\'s "quoted"')  # no literal string possible
    config.set_option(file, "ignore", ["*.txt", "/docs/"])  # a value over several lines
    config.set_option(file, "launch_command", ["lutris", "lutris:rungame/dao"])
    raw = config.load_raw(file)
    assert raw["game_install_dir"] == r"C:\GOG Games\Dragon Age"
    assert raw["terminal"] == 'it\'s "quoted"'
    assert raw["ignore"] == ["*.txt", "/docs/"] and raw["language"] == "de-de"
    assert raw["launch_command"] == ["lutris", "lutris:rungame/dao"]
    assert file.read_text().startswith("# mine\nignore = ['*.txt', '/docs/']\nlanguage")

    config.set_option(file, "ignore", None)  # back to the default
    config.set_option(file, "language", None)
    config.set_option(file, "max_texture_size", None)  # not there: nothing to do
    assert "ignore" not in config.load_raw(file) and "language" not in config.load_raw(file)
    assert file.read_text().startswith("# mine\ngame_install_dir")
    assert config.load_raw(tmp_path / "missing.toml") == {} and config.load_raw(None) == {}


def test_save_token(tmp_path: Path) -> None:
    path = config.save_token(tmp_path / "cfg", "  abc123 \n")
    assert path.read_text() == "abc123\n"
    assert config.load_token(tmp_path / "cfg", environment=False) == "abc123"
    if os.name != "nt":
        assert path.stat().st_mode & 0o777 == 0o600
        assert config.token_warning(tmp_path / "cfg") is None
    config.save_token(tmp_path / "cfg", "")
    assert not path.exists()


def test_validate_settings(tmp_path: Path) -> None:
    settings = {setting.key: setting for setting in config.SETTINGS}
    install = tmp_path / "Dragon Age"
    assert "not the game's install folder" in config.validate(
        settings["game_install_dir"], str(install)
    )
    (install / "BIN_SHIP").mkdir(parents=True)
    (install / "BIN_SHIP/daorigins.exe").write_bytes(b"MZ")  # found whatever the case
    assert config.validate(settings["game_install_dir"], str(install)) is None
    assert "start the game once" in config.validate(settings["game_user_dir"], str(tmp_path / "x"))
    assert config.validate(settings["game_user_dir"], str(tmp_path)) is None
    assert "needs {archive}" in config.validate(settings["unrar_command"], ["unrar", "x"])
    assert config.validate(settings["unrar_command"], ["unrar", "x", "{archive}"]) is None
    assert config.validate(settings["launch_command"], []) is not None
    assert config.validate(settings["game_app_id"], "abc") is not None
    assert config.validate(settings["max_texture_size"], 2048) is None
    assert config.validate(settings["max_texture_size"], 333) is not None
    assert config.validate(settings["language"], "de-de") is None
