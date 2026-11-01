from __future__ import annotations

from pathlib import Path

import httpx2
import pytest

from dao_modman.core import updates
from dao_modman.core.updates import latest as ask_pypi  # the real one, not the test's stand-in


@pytest.mark.parametrize(
    ("latest", "current", "expected"),
    [
        ("0.6.0", "0.5.0", True),
        ("0.5.1", "0.5.0", True),
        ("1.0", "0.9.9", True),
        ("0.5.0", "0.5.0", False),
        ("0.5", "0.5.0", False),  # the same version, written shorter
        ("0.4.9", "0.5.0", False),
        ("0.6.0rc1", "0.5.0", False),  # pre-releases are not offered
        ("0.6.0", "0.5.1.dev3+g1234567", False),  # running from source: never checked
    ],
)
def test_newer(latest: str, current: str, expected: bool) -> None:
    assert updates.newer(latest, current) is expected


def test_is_release() -> None:
    assert updates.is_release("0.5.0")
    assert not updates.is_release("0.5.1.dev3+g1234567")
    assert not updates.is_release("0+unknown")


async def test_latest_from_pypi() -> None:
    answers = iter(
        [
            httpx2.Response(200, json={"info": {"version": "0.6.0"}}),
            httpx2.Response(200, json={"info": {"version": "0.7.0b1"}}),
            httpx2.Response(503),
            httpx2.Response(200, text="not json"),
        ]
    )

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == updates.PYPI_URL
        return next(answers)

    transport = httpx2.MockTransport(handler)
    assert await ask_pypi(transport) == "0.6.0"
    assert await ask_pypi(transport) is None  # a pre-release
    assert await ask_pypi(transport) is None  # PyPI is down
    assert await ask_pypi(transport) is None  # an answer that makes no sense


def test_install_kind(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYAPP", raising=False)
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path / "venv"))
    assert updates.install_kind() == updates.Install(updates.OTHER, None)
    assert updates.install_kind().how == "pip install --upgrade dao-modman"

    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path / "uv/tools/dao-modman"))
    assert updates.install_kind().command == ["uv", "tool", "upgrade", "dao-modman"]
    monkeypatch.setattr(updates.sys, "prefix", str(tmp_path / "pipx/venvs/dao-modman"))
    assert updates.install_kind().command == ["pipx", "upgrade", "dao-modman"]

    exe = tmp_path / "Downloads/dao-modman.exe"
    monkeypatch.setenv("PYAPP", str(exe))  # PyApp passes its location
    assert updates.install_kind().kind != updates.PYAPP  # but only an existing file counts
    exe.parent.mkdir()
    exe.write_bytes(b"MZ")
    assert updates.install_kind() == updates.Install(updates.PYAPP, [str(exe), "self", "update"])
    monkeypatch.setenv("PYAPP", "1")  # set, but without the location
    assert updates.install_kind().kind != updates.PYAPP


def test_start_update(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[tuple[object, dict]] = []
    monkeypatch.setattr(updates.subprocess, "Popen", lambda args, **kw: started.append((args, kw)))
    monkeypatch.setattr(updates, "WINDOWS", True)
    updates.start_update([r"C:\My Games\dao-modman.exe", "self", "update"])
    line, options = started[0]
    assert line == (
        'cmd /s /c "timeout /t 2 >nul & "C:\\My Games\\dao-modman.exe" self update & pause"'
    )
    assert options == {"creationflags": updates.terminal.CREATE_NEW_CONSOLE}

    opened: list[list[str]] = []
    monkeypatch.setattr(updates, "WINDOWS", False)
    monkeypatch.setattr(updates.terminal, "open_window", lambda _t, argv: opened.append(argv))
    updates.start_update(["uv", "tool", "upgrade", "dao-modman"])
    assert opened[0][:2] == ["sh", "-c"] and "uv tool upgrade dao-modman" in opened[0][2]
