from __future__ import annotations

from pathlib import Path

import pytest

from dao_modman import __main__ as cli
from dao_modman.core import deploy, report
from dao_modman.core.config import Config
from dao_modman.core.library import Library

from .conftest import make_zip

OVERRIDE = "packages/core/override"


@pytest.fixture
def clashing(tmp_path: Path, library: Library) -> Library:
    """Four mods: A and C clash on two files, A, B and C on one, B ships one twice."""
    archives = {
        "Alpha": {"tex/robe.dds": "a", "sword.uti": "a", "helm.mmh": "a", "readme.txt": "a"},
        "Beta": {"x/helm.mmh": "b", "y/helm.mmh": "b2", "same.gda": "same"},
        "Gamma": {"Robe.DDS": "c", "sword.uti": "c", "helm.mmh": "c", "same.gda": "same"},
        "Delta": {"other.erf": "d", "readme.txt": "d"},
    }
    for name, files in archives.items():
        library.import_archive(make_zip(tmp_path / f"{name}.zip", files))
    return library


def test_groups_are_per_set_of_mods_with_the_winner_last(clashing: Library) -> None:
    groups = report.conflict_groups(clashing, deploy.build_plan(clashing))
    assert [(group.title, [c.name for c in group.conflicts]) for group in groups] == [
        ("#1 Alpha  <  #3 Gamma", ["Robe.DDS", "sword.uti"]),
        ("#1 Alpha  <  #2 Beta  <  #3 Gamma", ["helm.mmh"]),
    ]

    clashing.profile.move("alpha", 3)  # Alpha is now last and wins everything it ships
    groups = report.conflict_groups(clashing, deploy.build_plan(clashing))
    assert [group.title for group in groups] == [
        "#2 Gamma  <  #4 Alpha",
        "#1 Beta  <  #2 Gamma  <  #4 Alpha",
    ]


def test_text_report(clashing: Library) -> None:
    text = report.format_conflicts(clashing, deploy.build_plan(clashing))
    assert text.splitlines() == [
        "#1 Alpha  <  #3 Gamma   (2 files)",
        "  Robe.DDS",
        f"    #1 Alpha   {OVERRIDE}/alpha/tex/",
        f"    #3 Gamma   {OVERRIDE}/gamma/   ← used",
        "  sword.uti",
        f"    #1 Alpha   {OVERRIDE}/alpha/",
        f"    #3 Gamma   {OVERRIDE}/gamma/   ← used",
        "",
        "#1 Alpha  <  #2 Beta  <  #3 Gamma   (1 file)",
        "  helm.mmh",
        f"    #1 Alpha   {OVERRIDE}/alpha/",
        f"    #2 Beta    {OVERRIDE}/beta/x/",
        f"               {OVERRIDE}/beta/y/",
        f"    #3 Gamma   {OVERRIDE}/gamma/   ← used",
    ]


def test_report_uses_display_names_and_lists_merges(tmp_path: Path, library: Library) -> None:
    cfg = '<morph_config><hairs><resource name="{}" /></hairs></morph_config>'
    for name in ("one", "two"):
        mod = library.import_archive(
            make_zip(tmp_path / f"{name}.zip", {"chargenmorphcfg.xml": cfg.format(name)})
        )
        mod.name = f"Hair pack {name}"
    text = report.format_conflicts(library, deploy.build_plan(library))
    assert text.splitlines() == [
        "Merged (no mod loses):",
        f"  {OVERRIDE}/chargenmorphcfg.xml  from #1 Hair pack one, #2 Hair pack two",
    ]


def test_cli(
    clashing: Library, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: Config(dirs=clashing.dirs))
    assert cli.main(["conflicts"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("#1 Alpha  <  #3 Gamma   (2 files)\n")
    assert "readme" not in out and "same.gda" not in out and "other.erf" not in out

    for mod_id in ("alpha", "beta"):
        clashing.profile.entry(mod_id).enabled = False
    clashing.save_profile()
    assert cli.main(["conflicts"]) == 0
    assert capsys.readouterr().out == "no conflicts\n"


def test_report_marks_and_orders_by_status(clashing: Library) -> None:
    from dao_modman.core.models import Overwrite

    gamma = clashing.mods["gamma"]
    gamma.overwrites = [Overwrite("alpha", ["sword.uti"])]
    text = report.format_conflicts(clashing, deploy.build_plan(clashing))
    lines = text.splitlines()
    # inside a group the accepted file comes after the open one
    assert lines[:2] == ["#1 Alpha  <  #3 Gamma   (2 files)", "  Robe.DDS"]
    assert "  sword.uti   \u2713 accepted" in lines

    gamma.overwrites = [Overwrite("alpha")]
    lines = report.format_conflicts(clashing, deploy.build_plan(clashing)).splitlines()
    # the fully accepted group moves below the one that still has an open conflict
    assert lines[0] == "#1 Alpha  <  #2 Beta  <  #3 Gamma   (1 file)"
    assert "#1 Alpha  <  #3 Gamma   (2 files, accepted)" in lines

    clashing.profile.move("gamma", -2)  # Gamma now loads first and loses
    lines = report.format_conflicts(clashing, deploy.build_plan(clashing)).splitlines()
    assert lines[0].startswith("#1 Gamma  <")  # errors come first
    # the file is named as the winner ships it, which is now Alpha's copy
    assert (
        "  robe.dds   \u2717 wrong order: #1 Gamma is set to overwrite #2 Alpha but loads before it"
        in lines
    )


def test_cli_accept(
    clashing: Library, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: Config(dirs=clashing.dirs))

    def statuses() -> dict[str, str]:
        library = Library(clashing.dirs)
        return {c.name: c.status for c in deploy.build_plan(library).conflicts}

    # an unknown name is an error and nothing is written
    assert cli.main(["conflicts", "accept", "sword.uti", "nope.gda"]) == 1
    assert "nope.gda" in capsys.readouterr().err
    assert set(statuses().values()) == {"open"}

    # by file name (case-insensitive, globs)
    assert cli.main(["conflicts", "accept", "SWORD.uti"]) == 0
    assert capsys.readouterr().out == "accepted sword.uti: gamma overwrites alpha\n"
    assert statuses() == {"helm.mmh": "open", "Robe.DDS": "open", "sword.uti": "accepted"}
    assert cli.main(["conflicts", "accept", "*.dds"]) == 0
    assert statuses()["Robe.DDS"] == "accepted"

    # by mod id: every open conflict the mod takes part in, winning or losing
    assert cli.main(["conflicts", "accept", "beta"]) == 0
    assert capsys.readouterr().out.endswith("accepted helm.mmh: gamma overwrites alpha, beta\n")
    assert set(statuses().values()) == {"accepted"}
    assert cli.main(["conflicts", "accept", "--all"]) == 0
    assert capsys.readouterr().out == "no open conflicts\n"
    assert cli.main(["conflicts"]) == 0

    # entries are per file, so a new clash between the same mods is still reported
    overwrites = Library(clashing.dirs).mods["gamma"].overwrites
    assert sorted((o.mod, sorted(o.files)) for o in overwrites) == [
        ("alpha", ["Robe.DDS", "helm.mmh", "sword.uti"]),
        ("beta", ["helm.mmh"]),
    ]

    # a wrong load order makes the report fail and cannot be accepted
    library = Library(clashing.dirs)
    library.profile.move("gamma", -2)
    library.save_profile()
    assert cli.main(["conflicts"]) == 1
    assert "wrong order" in capsys.readouterr().out
    assert cli.main(["conflicts", "accept", "--all"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("no open conflicts\nnot accepted: ") and "wrong order" in out


def test_cli_accept_everything(
    clashing: Library, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cli, "load_config", lambda: Config(dirs=clashing.dirs))
    # accepting everything must be asked for explicitly
    assert cli.main(["conflicts", "accept"]) == 2
    assert cli.main(["conflicts", "accept", "--all", "sword.uti"]) == 2
    assert "--all" in capsys.readouterr().err
    assert {c.status for c in deploy.build_plan(Library(clashing.dirs)).conflicts} == {"open"}

    assert cli.main(["conflicts", "accept", "--all"]) == 0
    assert capsys.readouterr().out.count("accepted ") == 3
    library = Library(clashing.dirs)
    assert {c.status for c in deploy.build_plan(library).conflicts} == {"accepted"}


def test_rules_about_a_removed_mod_are_reported_as_unused(
    clashing: Library, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from dao_modman.core.models import Overwrite

    clashing.mods["gamma"].overwrites = [Overwrite("alpha", ["sword.uti", "Robe.DDS"])]
    clashing.mods["delta"].overwrites = [Overwrite("alpha")]
    clashing.profile.entry("beta").enabled = False  # disabled is not "gone"
    clashing.mods["gamma"].overwrites.append(Overwrite("beta"))
    assert report.unused_rules(clashing) == []

    clashing.remove("alpha")
    assert [(mod_id, rule.mod) for mod_id, rule in report.unused_rules(clashing)] == [
        ("gamma", "alpha"),
        ("delta", "alpha"),
    ]
    for mod in clashing.mods.values():
        clashing.save_mod(mod)
    monkeypatch.setattr(cli, "load_config", lambda: Config(dirs=clashing.dirs))
    assert cli.main(["conflicts"]) == 0  # information, not an error
    assert capsys.readouterr().out.splitlines() == [
        "Unused overwrite rules (the other mod is not in the library):",
        "  #2 Gamma may overwrite alpha: sword.uti, Robe.DDS",
        "  #3 Delta may overwrite alpha: every file",
    ]
