from __future__ import annotations

import pytest

from dao_modman.core.ignore import IgnoreRules
from dao_modman.core.models import Mod, PathOp
from dao_modman.core.pathops import (
    PathOpError,
    default_mapping,
    kind,
    matches,
    resolve,
    select,
)

MAPPING = {
    "override/mod/a.erf": "a.erf",
    "override/mod/docs/readme.txt": "docs/readme.txt",
    "override/mod/Optional/b.erf": "Optional/b.erf",
    "override/mod/Optional/sub/c.erf": "Optional/sub/c.erf",
}


def test_default_mapping_dazip_skips_non_contents() -> None:
    mod = Mod(id="m", name="M", kind="dazip")
    files = ["Manifest.xml", "contents/addins/m/x.gda", "readme.txt"]
    assert default_mapping(mod, files) == {"addins/m/x.gda": "contents/addins/m/x.gda"}


def test_default_mapping_with_several_dazips_and_loose_files() -> None:
    mod = Mod(id="m", name="M")
    files = [
        "readme.txt",
        "extra/tex.dds",
        "One.dazip/Manifest.xml",
        "One.dazip/Contents/addins/one/a.erf",
        "One.dazip/docs.txt",
        "sub/Two.dazip/manifest.xml",
        "sub/Two.dazip/contents/packages/core/data/two.erf",
        "NotADazip/Manifest.xml",  # no Contents folder next to it
    ]
    assert default_mapping(mod, files) == {
        "addins/one/a.erf": "One.dazip/Contents/addins/one/a.erf",
        "packages/core/data/two.erf": "sub/Two.dazip/contents/packages/core/data/two.erf",
        "packages/core/override/m/readme.txt": "readme.txt",
        "packages/core/override/m/extra/tex.dds": "extra/tex.dds",
        "packages/core/override/m/NotADazip/Manifest.xml": "NotADazip/Manifest.xml",
    }


def test_kind() -> None:
    ignored = IgnoreRules().matches
    dazip = ["X.dazip/Manifest.xml", "X.dazip/Contents/addins/x/a.erf"]
    assert kind(["a.erf", "docs/readme.txt"], ignored) == "override"
    assert kind(["Manifest.xml", "Contents/a.erf", "readme.txt"], ignored) == "dazip"
    assert kind([*dazip, "ReadMe.TXT", "manual.pdf", "docs/README"], ignored) == "dazip"
    assert kind([*dazip, "readme.txt", "textures/t.dds"], ignored) == "mixed"
    assert kind([*dazip, "X.dazip/extra.erf"], ignored) == "dazip"  # inside the dazip, not loose
    links = ["z - Manual.URL", "preview.JPG", "shots/1.png", "notes.docx", "page.html"]
    assert kind([*dazip, *links], ignored) == "dazip"
    assert kind([*dazip, "tex.dds"], ignored) == "mixed"  # textures are game files
    assert kind([*dazip, "chargenmorphcfg.xml"], ignored) == "mixed"
    assert kind([*dazip, "readme.txt"]) == "mixed"  # without rules, every loose file counts


def test_default_mapping_leaves_ignored_files_out() -> None:
    mod = Mod(id="m", name="M")
    files = ["a.erf", "readme.txt", "X.dazip/Manifest.xml", "X.dazip/Contents/addins/x/notes.txt"]
    files += ["X.dazip/Contents/addins/x/b.erf"]
    assert default_mapping(mod, files, IgnoreRules().matches) == {
        "packages/core/override/m/a.erf": "a.erf",
        "addins/x/b.erf": "X.dazip/Contents/addins/x/b.erf",
    }


def test_ignore_rules() -> None:
    rules = IgnoreRules(["*.TXT", "/top/", "docs/", "!docs/keep.erf", "*readme*"])
    for path in ("a.txt", "x/y/B.Txt", "top/a.erf", "x/docs/a.erf", "Docs/a.erf", "x/ReadMe_de"):
        assert rules.matches(path), path
    for path in ("a.erf", "x/top/a.erf", "docs.erf"):
        assert not rules.matches(path), path
    assert not IgnoreRules([]).matches("readme.txt")


def test_matches() -> None:
    assert matches("a/b", "a/b")
    assert matches("a/b", "A/B/c.txt")
    assert not matches("a/b", "a/bc")
    assert matches("*.txt", "readme.txt")
    assert matches("a/*/docs", "a/x/docs/readme.txt")
    assert not matches("*.txt", "a/b.erf")


def installed(ops: list[PathOp], ignored=None) -> list[str]:
    return list(select(MAPPING, ignored, ops)[0])


def test_exclude_file_and_directory_case_insensitive() -> None:
    ops = [PathOp("exclude", "override/mod/optional"), PathOp("exclude", "override/mod/a.erf")]
    chosen, left_out = select(MAPPING, None, ops)
    assert list(chosen) == ["override/mod/docs/readme.txt"]
    assert list(left_out) == [
        "override/mod/Optional/b.erf",
        "override/mod/Optional/sub/c.erf",
        "override/mod/a.erf",
    ]


def test_exclude_glob() -> None:
    assert installed([PathOp("exclude", "*/readme.txt"), PathOp("exclude", "*.erf")]) == []


def test_include_what_an_ignore_pattern_leaves_out() -> None:
    ignored = IgnoreRules().matches  # readme.txt is ignored by default
    assert "override/mod/docs/readme.txt" not in installed([], ignored)
    assert "override/mod/docs/readme.txt" in installed(
        [PathOp("include", "override/mod/docs/readme.txt")], ignored
    )


def test_ops_run_in_order() -> None:
    folder, file = "override/mod/Optional", "override/mod/Optional/sub/c.erf"
    # exclude a folder, include one file back
    ops = [PathOp("exclude", folder), PathOp("include", file)]
    assert installed(ops) == [file, "override/mod/a.erf", "override/mod/docs/readme.txt"]
    # reversed, the exclude wins again
    assert file not in installed(ops[::-1])


def test_names_with_glob_characters_are_matched_literally() -> None:
    mapping = {"a/tex[1].dds": "1", "a/tex1.dds": "2"}
    assert select(mapping, None, [PathOp("exclude", "a/tex[1].dds")])[0] == {"a/tex1.dds": "2"}
    assert select(mapping, None, [PathOp("exclude", "a/*.dds")])[0] == {}  # a real glob


def test_input_mapping_is_not_mutated() -> None:
    before = dict(MAPPING)
    select(MAPPING, None, [PathOp("exclude", "override")])
    assert before == MAPPING


@pytest.mark.parametrize(
    "op",
    [PathOp("exclude", ".."), PathOp("include", "/abs/../.."), PathOp("remove", "a"),
     PathOp("rename", "a")],
)  # fmt: skip
def test_invalid_ops(op: PathOp) -> None:
    with pytest.raises(PathOpError):
        select(MAPPING, None, [op])


def test_resolve_uses_mod_ops() -> None:
    mod = Mod(id="m", name="M", ops=[PathOp("exclude", "packages/core/override/m/docs")])
    assert resolve(mod, ["a.erf", "docs/x.erf"]) == {"packages/core/override/m/a.erf": "a.erf"}
    # the ignore patterns leave the readme out, an include brings it back
    mod.ops = [PathOp("include", "packages/core/override/m/readme.txt")]
    assert resolve(mod, ["a.erf", "readme.txt"], IgnoreRules().matches) == {
        "packages/core/override/m/a.erf": "a.erf",
        "packages/core/override/m/readme.txt": "readme.txt",
    }
