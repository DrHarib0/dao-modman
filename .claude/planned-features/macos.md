# Planned: macOS

## Why

dao-modman runs on Linux and Windows; the README says macOS is not supported yet. Testers are
asked for in the README's Contributing section.

## What a port would touch (learned from the Windows port)

- The game: DA:O had a Mac release in 2009 (through a compatibility wrapper); whether it is
  still sold or runs on current macOS is to be checked. Otherwise the Windows game under
  CrossOver/Wine, set up like Lutris on Linux: `game_user_dir`, `game_install_dir` and
  `launch_command` in config.toml (already supported, also through the settings screen).
- Folders: `core/paths.py` uses XDG on Linux and `%APPDATA%` / `%LOCALAPPDATA%` on Windows
  (via `core/windows.py`); macOS would use `~/Library/Application Support/dao-modman`.
- The nxm:// handler (`core/nxm_handler.py`): macOS registers URL schemes through an app
  bundle's `Info.plist`, not a desktop file or the registry; a minimal `.app` wrapper would be
  needed.
- Opening a terminal for incoming links (`core/terminal.py`): `open -a Terminal` (or the
  user's `terminal` setting).
- Already prepared: `core/opener.py` uses `open` on macOS; `.rar` via `unrar`/`7z` from
  Homebrew on PATH.
- CI: a `macos-latest` job running the test suite, like `test-windows`.

## Open questions

- Is there demand, and someone to test on a Mac?
