from __future__ import annotations

import pytest

from dao_modman.core.ignore import IgnoreRules
from dao_modman.core.models import Mod, PathOp
from dao_modman.core.pathops import (
    PathOpError,
    apply_ops,
    default_mapping,
    kind,
    matches,
    resolve,
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


def test_remove_file_and_directory_case_insensitive() -> None:
    result = apply_ops(
        MAPPING, [PathOp("remove", "override/mod/optional"), PathOp("remove", "override/mod/a.erf")]
    )
    assert list(result) == ["override/mod/docs/readme.txt"]


def test_remove_glob() -> None:
    result = apply_ops(MAPPING, [PathOp("remove", "*/readme.txt"), PathOp("remove", "*.erf")])
    assert result == {}


def test_rename_directory_and_file() -> None:
    result = apply_ops(
        MAPPING,
        [
            PathOp("rename", "override/mod/Optional", "override/mod/main"),
            PathOp("rename", "override/mod/a.erf", "elsewhere/z.erf"),
        ],
    )
    assert result == {
        "elsewhere/z.erf": "a.erf",
        "override/mod/docs/readme.txt": "docs/readme.txt",
        "override/mod/main/b.erf": "Optional/b.erf",
        "override/mod/main/sub/c.erf": "Optional/sub/c.erf",
    }


def test_ops_run_in_order() -> None:
    ops = [
        PathOp("rename", "override/mod/Optional", "override/mod/x"),
        PathOp("remove", "override/mod/x/sub"),
    ]
    assert "override/mod/x/b.erf" in apply_ops(MAPPING, ops)
    assert "override/mod/x/sub/c.erf" not in apply_ops(MAPPING, ops)
    # reversed, the remove no longer matches anything
    assert "override/mod/x/sub/c.erf" in apply_ops(MAPPING, ops[::-1])


def test_rename_replaces_existing_destination() -> None:
    result = apply_ops(
        MAPPING, [PathOp("rename", "override/mod/Optional/b.erf", "override/mod/A.erf")]
    )
    assert result["override/mod/A.erf"] == "Optional/b.erf"
    assert "override/mod/a.erf" not in result


def test_names_with_glob_characters_are_matched_literally() -> None:
    mapping = {"a/tex[1].dds": "1", "a/tex1.dds": "2"}
    assert apply_ops(mapping, [PathOp("remove", "a/tex[1].dds")]) == {"a/tex1.dds": "2"}
    assert apply_ops(mapping, [PathOp("rename", "a/tex[1].dds", "b.dds")]) == {
        "a/tex1.dds": "2",
        "b.dds": "1",
    }
    # rename never globs
    assert apply_ops(mapping, [PathOp("rename", "a/*.dds", "b")]) == mapping


def test_input_mapping_is_not_mutated() -> None:
    before = dict(MAPPING)
    apply_ops(MAPPING, [PathOp("remove", "override")])
    assert before == MAPPING


@pytest.mark.parametrize(
    "op",
    [
        PathOp("rename", "a", "../outside"),
        PathOp("rename", "a", "/abs/../.."),
        PathOp("rename", "a"),
        PathOp("remove", ".."),
        PathOp("chmod", "a"),
    ],
)
def test_invalid_ops(op: PathOp) -> None:
    with pytest.raises(PathOpError):
        apply_ops(MAPPING, [op])


def test_resolve_uses_mod_ops() -> None:
    mod = Mod(id="m", name="M", ops=[PathOp("remove", "packages/core/override/m/docs")])
    assert resolve(mod, ["a.erf", "docs/readme.txt"]) == {"packages/core/override/m/a.erf": "a.erf"}
