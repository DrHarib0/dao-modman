"""Library revisions: the library's metadata history in git, as a debugging aid.

Opt-in: nothing happens unless the data directory itself is a git repository
(`dao-modman revisions init`). Only metadata is tracked (mod.toml files, profile.toml,
deployed.json), never archives or extracted mod files. A missing or failing git never gets
in the way of the operation that triggered the snapshot.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

GITIGNORE = """# written by dao-modman: only the library's metadata is tracked
/downloads/
/mods/*/files/
/mods/.import-*/
*.tmp
"""

# Independent of the user's git setup: fixed author, no signing, no hooks.
_GIT = [
    "git",
    "-c", "user.name=dao-modman",
    "-c", "user.email=dao-modman@localhost",
    "-c", "commit.gpgsign=false",
    "-c", "core.hooksPath=/dev/null",
]  # fmt: skip


def _git(data_dir: Path, *args: str) -> bool:
    try:
        result = subprocess.run(
            [*_GIT, "-C", str(data_dir), *args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def enabled(data_dir: Path) -> bool:
    """True if the data directory itself is a git repository (a parent repo does not count)."""
    return (data_dir / ".git").exists()


def snapshot(data_dir: Path, message: str) -> bool:
    """Commit the current metadata. Returns whether a revision was recorded."""
    if not enabled(data_dir):
        return False
    try:
        ignore = data_dir / ".gitignore"
        if not ignore.exists():
            ignore.write_text(GITIGNORE, encoding="utf-8")
    except OSError:
        return False
    # commit fails harmlessly when nothing changed, or when another process holds the lock
    return _git(data_dir, "add", "-A") and _git(data_dir, "commit", "-q", "-m", message)


def init(data_dir: Path) -> bool:
    """Start tracking the library. Returns False if git is not usable."""
    data_dir.mkdir(parents=True, exist_ok=True)
    if not enabled(data_dir) and not _git(data_dir, "init", "-q"):
        return False
    snapshot(data_dir, "start tracking the library")
    return enabled(data_dir)
