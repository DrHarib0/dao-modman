from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from dao_modman.core.library import Library
from dao_modman.core.paths import Dirs

MANIFEST = """<?xml version="1.0" encoding="utf-8"?>
<Manifest Type="AddIn">
  <AddInsList>
    <AddInItem UID="{uid}" Name="{uid}" ExtendedModuleUID="Single Player" Version="1.2"
               Enabled="1" State="2" Format="1">
      <Title DefaultText="{title}" />
    </AddInItem>
  </AddInsList>
</Manifest>
"""


def make_zip(path: Path, files: dict[str, str | bytes]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return path


def make_dazip(path: Path, uid: str = "test_addin", title: str = "Test AddIn") -> Path:
    return make_zip(
        path,
        {
            "Manifest.xml": MANIFEST.format(uid=uid, title=title),
            f"Contents/addins/{uid}/module/data/a.gda": "gda",
            f"Contents/packages/core/override/{uid}/b.erf": "erf",
        },
    )


@pytest.fixture
def dirs(tmp_path: Path) -> Dirs:
    return Dirs(data=tmp_path / "data", downloads=tmp_path / "data" / "downloads")


@pytest.fixture
def library(dirs: Dirs) -> Library:
    return Library(dirs)


@pytest.fixture
def user_dir(tmp_path: Path) -> Path:
    path = tmp_path / "Dragon Age"
    path.mkdir()
    return path
