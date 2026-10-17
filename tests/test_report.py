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


def test_groups_follow_the_load_order(clashing: Library) -> None:
    lines = report.format_conflicts(clashing, deploy.build_plan(clashing)).splitlines()
    groups = [line for line in lines if not line.startswith(" ") and line]
    assert groups == [
        "#1 Alpha  <  #3 Gamma   (2 files)",
        "#1 Alpha  <  #2 Beta  <  #3 Gamma   (1 file)",
    ]

    clashing.profile.move("gamma", -2)  # Gamma now loads first and loses
    lines = report.format_conflicts(clashing, deploy.build_plan(clashing)).splitlines()
    groups = [line for line in lines if not line.startswith(" ") and line]
    assert groups == [
        "#1 Gamma  <  #2 Alpha   (2 files)",
        "#1 Gamma  <  #2 Alpha  <  #3 Beta   (1 file)",
    ]
    assert "  robe.dds" in lines  # named as the winner ships it, now Alpha's copy
