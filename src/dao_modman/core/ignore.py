"""Files inside mods that are never deployed, selected with .gitignore patterns.

The game only loads its own resource types; readmes, pictures and the like that mods ship
next to them would just clutter the game directory.
"""

from __future__ import annotations

from collections.abc import Iterable

import pathspec

# .gitignore syntax, matched against paths inside a mod's files, ignoring case. Game formats
# that look similar (.dds and .tga textures, .xml) are deliberately not listed.
DEFAULT_PATTERNS = (
    # text
    "*.txt",
    "*.pdf",
    "*.rtf",
    "*.doc",
    "*.docx",
    "*.odt",
    "*.md",
    "*.htm",
    "*.html",
    "*.nfo",
    "*.log",
    # links
    "*.url",
    "*.lnk",
    "*.webloc",
    # pictures
    "*.jpg",
    "*.jpeg",
    "*.png",
    "*.gif",
    "*.bmp",
    "*.webp",
    # readmes without a telling extension
    "*readme*",
    # leftovers of the packer's operating system
    "Thumbs.db",
    "desktop.ini",
    ".DS_Store",
    "__MACOSX/",
)


class IgnoreError(ValueError):
    pass


class IgnoreRules:
    def __init__(self, patterns: Iterable[str] = DEFAULT_PATTERNS) -> None:
        self.patterns = list(patterns)
        try:
            lines = [pattern.lower() for pattern in self.patterns]
            self._spec = pathspec.GitIgnoreSpec.from_lines(lines)
        except Exception as exc:  # pathspec raises its own error types for bad patterns
            raise IgnoreError(f"invalid ignore pattern: {exc}") from exc

    def matches(self, path: str) -> bool:
        """True if the file at `path` (relative, `/`-separated) is ignored."""
        return self._spec.match_file(path.lower())
