from __future__ import annotations

import os
from pathlib import Path

import pytest

from dao_modman import __main__ as cli
from dao_modman.core import nxm_handler


@pytest.fixture
def linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Registration as on Linux, in tmp_path; returns the xdg-mime calls."""
    calls: list[list[str]] = []
    monkeypatch.setattr(nxm_handler, "WINDOWS", False)
    monkeypatch.setattr(nxm_handler.Path, "home", lambda: tmp_path)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.delenv("NEXUS_API_TOKEN", raising=False)
    monkeypatch.setattr(nxm_handler.shutil, "which", lambda _: "/usr/bin/xdg-mime")
    monkeypatch.setattr(nxm_handler.subprocess, "run", lambda command, **_: calls.append(command))
    return calls


@pytest.fixture
def registry(monkeypatch: pytest.MonkeyPatch) -> dict[str, dict[str, str]]:
    """Registration as on Windows, with a dict for HKEY_CURRENT_USER."""
    keys: dict[str, dict[str, str]] = {}
    monkeypatch.setattr(nxm_handler, "WINDOWS", True)
    monkeypatch.setattr(nxm_handler.windows, "windowless_python", lambda: r"C:\py\pythonw.exe")

    def value(subkey: str, name: str = "", *, machine: bool = True) -> str | None:
        assert not machine  # only what the user has set counts
        return keys.get(subkey, {}).get(name) or None

    def set_values(subkey: str, values: dict[str, str]) -> bool:
        keys.setdefault(subkey, {}).update(values)
        return True

    def delete(subkey: str) -> bool:
        gone = [key for key in keys if key == subkey or key.startswith(subkey + "\\")]
        for key in gone:
            del keys[key]
        return bool(gone)

    monkeypatch.setattr(nxm_handler.windows, "registry_value", value)
    monkeypatch.setattr(nxm_handler.windows, "set_user_registry", set_values)
    monkeypatch.setattr(nxm_handler.windows, "delete_user_registry", delete)
    return keys


def test_install_and_uninstall_on_linux(tmp_path: Path, linux: list[list[str]]) -> None:
    lines = nxm_handler.install()
    desktop = tmp_path / ".local/share/applications/dao-modman-nxm.desktop"
    entry = desktop.read_text()
    assert "x-scheme-handler/nxm" in entry
    assert "Terminal=false" in entry  # the handler opens a terminal itself when needed
    assert entry.count("-m dao_modman nxm %u") == 1
    assert linux == [["xdg-mime", "default", "dao-modman-nxm.desktop", "x-scheme-handler/nxm"]]
    assert lines[-1] == "registered as handler for nxm:// links"

    # what xdg-mime wrote, next to another program's entries
    mimeapps = tmp_path / "config/mimeapps.list"
    mimeapps.parent.mkdir()
    mimeapps.write_text(
        "[Default Applications]\nx-scheme-handler/nxm=dao-modman-nxm.desktop;\n"
        "text/html=firefox.desktop\n[Added Associations]\n"
        "x-scheme-handler/nxm=other.desktop;dao-modman-nxm.desktop;\n"
    )
    lines = nxm_handler.uninstall()
    assert not desktop.exists()
    assert mimeapps.read_text() == (
        "[Default Applications]\ntext/html=firefox.desktop\n[Added Associations]\n"
        "x-scheme-handler/nxm=other.desktop;\n"
    )
    assert len(lines) == 2
    assert nxm_handler.uninstall() == ["dao-modman was not registered as nxm:// handler"]


def test_status_on_linux(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nxm_handler, "WINDOWS", False)
    monkeypatch.setattr(nxm_handler.Path, "home", lambda: tmp_path)
    monkeypatch.setattr(nxm_handler.shutil, "which", lambda _: "/usr/bin/xdg-mime")
    default = {"out": ""}

    def run(command: list[str], **_: object) -> nxm_handler.subprocess.CompletedProcess:
        return nxm_handler.subprocess.CompletedProcess(command, 0, default["out"] + "\n", "")

    monkeypatch.setattr(nxm_handler.subprocess, "run", run)
    assert nxm_handler.status() == (nxm_handler.NONE, "")
    default["out"] = "other.desktop"
    assert nxm_handler.status() == (nxm_handler.OTHER, "other.desktop")
    default["out"] = "dao-modman-nxm.desktop"
    assert nxm_handler.status() == (nxm_handler.OURS, "")


def test_status_on_windows(registry: dict[str, dict[str, str]], monkeypatch) -> None:
    def value(subkey: str, name: str = "", *, machine: bool = True) -> str | None:
        return registry.get(subkey, {}).get(name) or None  # machine-wide entries count here

    monkeypatch.setattr(nxm_handler.windows, "registry_value", value)
    assert nxm_handler.status() == (nxm_handler.NONE, "")
    registry[nxm_handler.REGISTRY_COMMAND] = {"": '"Vortex.exe" -d "%1"'}
    assert nxm_handler.status() == (nxm_handler.OTHER, '"Vortex.exe" -d "%1"')
    nxm_handler.install()
    assert nxm_handler.status() == (nxm_handler.OURS, "")


def test_install_without_xdg_mime(linux: list[list[str]], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(nxm_handler.shutil, "which", lambda _: None)
    with pytest.raises(nxm_handler.HandlerError, match="xdg-mime was not found"):
        nxm_handler.install()


def test_install_and_uninstall_on_windows(registry: dict[str, dict[str, str]]) -> None:
    lines = nxm_handler.install()
    assert registry == {
        r"Software\Classes\nxm": {"": "URL:nxm Protocol", "URL Protocol": ""},
        r"Software\Classes\nxm\shell\open\command": {
            "": r'"C:\py\pythonw.exe" -m dao_modman nxm "%1"'
        },
    }
    assert len(lines) == 1  # nothing replaced
    assert nxm_handler.install() == lines  # again: still nothing foreign replaced

    assert nxm_handler.uninstall()[0].startswith("removed dao-modman")
    assert registry == {}
    assert nxm_handler.uninstall() == ["dao-modman was not registered as nxm:// handler"]


def test_another_programs_handler_on_windows(registry: dict[str, dict[str, str]]) -> None:
    vortex = r'"C:\Program Files\Black Tree Gaming Ltd\Vortex\Vortex.exe" -d "%1"'
    registry[nxm_handler.REGISTRY_COMMAND] = {"": vortex}
    assert "left the nxm:// handler alone" in nxm_handler.uninstall()[0]
    assert registry[nxm_handler.REGISTRY_COMMAND] == {"": vortex}

    lines = nxm_handler.install()
    assert lines[1] == f"replaced the previous handler: {vortex}"
    assert "dao_modman" in registry[nxm_handler.REGISTRY_COMMAND][""]


def test_cli_install_warns_about_unreachable_token(
    tmp_path: Path, linux: list[list[str]], monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    assert cli.main(["nxm", "--install-handler"]) == 0
    assert "may not find your API token" in capsys.readouterr().out
    monkeypatch.setenv("NEXUS_API_TOKEN", "abc")  # set in this shell: the browser lacks it
    assert cli.main(["nxm", "--install-handler"]) == 0
    assert "may not find your API token" in capsys.readouterr().out

    config_dir = tmp_path / "config/dao-modman"
    config_dir.mkdir(parents=True)
    (config_dir / ".nexus-token").write_text("abc\n")
    (config_dir / ".nexus-token").chmod(0o644)
    assert cli.main(["nxm", "--install-handler"]) == 0
    out = capsys.readouterr().out
    assert "may not find" not in out
    if os.name != "nt":  # chmod cannot make a file readable by others on Windows
        assert "can be read by other users" in out

    assert cli.main(["nxm", "--uninstall-handler"]) == 0
    assert "removed" in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv",
    [
        ["nxm"],
        ["nxm", "nxm://x", "--install-handler"],
        ["nxm", "--install-handler", "--uninstall-handler"],
    ],
)
def test_cli_wants_one_thing_to_do(argv: list[str]) -> None:
    with pytest.raises(SystemExit):
        cli.main(argv)
