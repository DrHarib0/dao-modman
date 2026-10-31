from __future__ import annotations

import pytest

from dao_modman.core import terminal

ARGV = ["python", "-m", "dao_modman", "--nxm", "nxm://x"]


@pytest.fixture
def installed(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Names that `shutil.which` finds; KDE and $TERMINAL are unset unless a test says so."""
    names: set[str] = set()
    monkeypatch.setattr(terminal.shutil, "which", lambda name: name if name in names else None)
    monkeypatch.setattr(terminal, "_kde_terminal", lambda: None)
    monkeypatch.delenv("TERMINAL", raising=False)
    return names


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("kitty", ["kitty", *ARGV]),
        ("kitty --class dao-modman", ["kitty", "--class", "dao-modman", *ARGV]),
        ("konsole", ["konsole", "-e", *ARGV]),
        ("/usr/bin/gnome-terminal", ["/usr/bin/gnome-terminal", "--", *ARGV]),
        ("wezterm", ["wezterm", "start", "--", *ARGV]),
        ("alacritty", ["alacritty", "-e", *ARGV]),
    ],
)
def test_command_per_terminal(installed: set[str], configured: str, expected: list[str]) -> None:
    installed.add(configured.split()[0])
    assert terminal.command(configured, ARGV) == expected


def test_resolution_order(installed: set[str], monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(terminal.TerminalError):
        terminal.resolve(None)

    installed.add("xterm")
    assert terminal.resolve(None) == ["xterm"]  # last resort: a known terminal
    installed.add("konsole")
    assert terminal.resolve(None) == ["konsole"]  # earlier in the known list
    installed.add("alacritty")
    monkeypatch.setattr(terminal, "_kde_terminal", lambda: "alacritty")
    assert terminal.resolve(None) == ["alacritty"]  # the desktop's setting
    installed.add("xdg-terminal-exec")
    assert terminal.resolve(None) == ["xdg-terminal-exec"]
    installed.add("foot")
    monkeypatch.setenv("TERMINAL", "foot")
    assert terminal.resolve(None) == ["foot"]
    installed.add("kitty")
    assert terminal.resolve("kitty") == ["kitty"]  # config.toml wins

    # a configured terminal that is not installed falls through to the next candidate
    assert terminal.resolve("ghostty") == ["foot"]
    assert terminal.resolve('broken "quote') == ["foot"]


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch) -> list[tuple[list[str], dict]]:
    calls: list[tuple[list[str], dict]] = []
    monkeypatch.setattr(terminal.subprocess, "Popen", lambda argv, **kw: calls.append((argv, kw)))
    return calls


def test_open_window_on_linux(installed: set[str], started, monkeypatch) -> None:
    monkeypatch.setattr(terminal, "WINDOWS", False)
    installed.add("konsole")
    terminal.open_window(None, ARGV)
    argv, options = started[0]
    assert argv == ["konsole", "-e", *ARGV]
    assert options["start_new_session"] and options["stdin"] == terminal.subprocess.DEVNULL


def test_open_window_on_windows(started, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(terminal, "WINDOWS", True)
    terminal.open_window(None, ARGV)  # a console of its own: Windows picks the terminal
    assert started[0] == (ARGV, {"creationflags": terminal.CREATE_NEW_CONSOLE})
    terminal.open_window("wt -w new", ARGV)  # or the one from config.toml, around the command
    assert started[1] == (["wt", "-w", "new", *ARGV], {})

    def fail(argv: list[str], **_: object) -> None:
        raise FileNotFoundError("not found")

    monkeypatch.setattr(terminal.subprocess, "Popen", fail)
    with pytest.raises(terminal.TerminalError, match="cannot start wt"):
        terminal.open_window("wt", ARGV)
