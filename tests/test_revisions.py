from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from dao_modman import __main__ as cli
from dao_modman.core import deploy, revisions
from dao_modman.core.config import Config
from dao_modman.core.library import Library
from dao_modman.core.models import PathOp
from dao_modman.core.paths import Dirs

from .conftest import make_override, make_zip


def git(data_dir: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(data_dir), *args], capture_output=True, text=True, check=True
    )
    return result.stdout


def log(data_dir: Path) -> list[str]:
    """Commit messages, oldest first."""
    return git(data_dir, "log", "--reverse", "--format=%s").splitlines()


def test_nothing_happens_without_a_repository(tmp_path: Path, library: Library) -> None:
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a"}))
    assert not revisions.enabled(library.dirs.data)
    assert not revisions.snapshot(library.dirs.data, "anything")
    assert not (library.dirs.data / ".git").exists()
    assert not (library.dirs.data / ".gitignore").exists()


def test_every_change_becomes_a_revision(tmp_path: Path, dirs: Dirs, user_dir: Path) -> None:
    library = Library(dirs)
    library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a"}))
    assert revisions.init(dirs.data)
    assert log(dirs.data) == ["start tracking the library"]

    package = library.import_archive(make_override(tmp_path / "Bodies.override"))
    library.set_option(package, library.options(package)[0].keys[0], "Big")
    library.profile.entry("one").enabled = False
    library.save_profile("disable one")
    one = library.mods["one"]
    one.ops = [PathOp("exclude", "packages/core/override/one/a.erf")]
    library.save_mod(one, "one: edit path operations (1)")
    deploy.apply(deploy.build_plan(library), user_dir, dirs.deployed_file)
    assert revisions.snapshot(dirs.data, "apply: 4 files")
    assert not revisions.snapshot(dirs.data, "nothing changed")  # no empty revisions
    library.remove("one")

    assert log(dirs.data) == [
        "start tracking the library",
        "import bodies from Bodies.override",
        "bodies: set option Humans/Body = Big",
        "disable one",
        "one: edit path operations (1)",
        "apply: 4 files",
        "remove one",
    ]

    # only metadata is tracked: no archives, no extracted mod files
    tracked = git(dirs.data, "ls-files").splitlines()
    assert tracked == [".gitignore", "deployed.json", "mods/bodies/mod.toml", "profile.toml"]
    assert git(dirs.data, "status", "--porcelain") == ""
    assert "dao-modman" in git(dirs.data, "log", "-1", "--format=%an")
    # the history shows what a change did
    diff = git(dirs.data, "show", "--format=", "HEAD~4", "--", "mods/bodies/mod.toml")
    assert '+"Humans/Body" = "Big"' in diff


def test_git_trouble_never_stops_an_operation(
    tmp_path: Path, dirs: Dirs, monkeypatch: pytest.MonkeyPatch
) -> None:
    library = Library(dirs)
    assert revisions.init(dirs.data)

    def broken(*args, **kwargs):
        raise OSError("git: command not found")

    monkeypatch.setattr(revisions.subprocess, "run", broken)
    mod = library.import_archive(make_zip(tmp_path / "one.zip", {"a.erf": "a"}))  # no exception
    assert Library(dirs).mods == {mod.id: mod}
    assert not revisions.snapshot(dirs.data, "still fine")

    monkeypatch.undo()
    (dirs.data / ".git/index.lock").write_text("")  # another process is committing
    library.profile.entry("one").enabled = False
    library.save_profile("disable one")  # skipped, not fatal
    (dirs.data / ".git/index.lock").unlink()
    assert revisions.snapshot(dirs.data, "caught up later")
    assert log(dirs.data)[-1] == "caught up later"


def test_cli_init(
    tmp_path: Path, dirs: Dirs, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    Library(dirs)
    monkeypatch.setattr(cli, "load_config", lambda: Config(dirs=dirs))
    assert cli.main(["revisions", "init"]) == 0
    out = capsys.readouterr().out
    assert str(dirs.data) in out and ".git" in out
    assert revisions.enabled(dirs.data)
    assert cli.main(["revisions", "init"]) == 0  # running it again is harmless
    assert log(dirs.data) == ["start tracking the library"]
