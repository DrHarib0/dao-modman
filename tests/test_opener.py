from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dao_modman.core import opener


@pytest.fixture
def readme(tmp_path: Path) -> Path:
    path = tmp_path / "readme.txt"
    path.write_text("r")
    return path


def test_linux_and_macos_use_the_desktop_command(
    readme: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []
    monkeypatch.setattr(opener.shutil, "which", lambda name: f"/usr/bin/{name}")
    monkeypatch.setattr(opener.subprocess, "Popen", lambda args, **_: calls.append(args))
    for platform, command in (("linux", "xdg-open"), ("darwin", "open")):
        monkeypatch.setattr(sys, "platform", platform)
        opener.open_file(readme)
        assert calls[-1] == [f"/usr/bin/{command}", str(readme)]


def test_windows_uses_startfile(readme: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[Path] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(opener.os, "startfile", started.append, raising=False)
    opener.open_file(readme)
    assert started == [readme]


def test_problems_are_reported(readme: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(opener.shutil, "which", lambda name: None)
    with pytest.raises(opener.OpenerError, match="xdg-open not found"):
        opener.open_file(readme)
    with pytest.raises(opener.OpenerError, match="not a file"):
        opener.open_file(readme.parent)


def test_programs_are_recognised() -> None:
    assert opener.is_executable(Path("Setup.EXE")) and opener.is_executable(Path("run.sh"))
    assert not opener.is_executable(Path("readme.txt"))
