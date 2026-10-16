from __future__ import annotations

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
    cwd, config_dir = tmp_path / "cwd", tmp_path / "config"
    cwd.mkdir()
    config_dir.mkdir()
    monkeypatch.delenv(config.TOKEN_VAR, raising=False)
    assert config.load_token(config_dir, cwd) is None
    (config_dir / ".env").write_text(f"{config.TOKEN_VAR}=from-config\n")
    assert config.load_token(config_dir, cwd) == "from-config"
    (cwd / ".env").write_text(f"{config.TOKEN_VAR}=from-cwd\n")
    assert config.load_token(config_dir, cwd) == "from-cwd"
    monkeypatch.setenv(config.TOKEN_VAR, "from-env")
    assert config.load_token(config_dir, cwd) == "from-env"


def test_load_config_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    monkeypatch.setenv(config.TOKEN_VAR, "tok")
    config_dir = tmp_path / "cfg" / paths.APP_NAME
    config_dir.mkdir(parents=True)
    (config_dir / "config.toml").write_text(
        f'game_user_dir = "{tmp_path}/game"\ndownloads_dir = "{tmp_path}/dl"\n'
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
