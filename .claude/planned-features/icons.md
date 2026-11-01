# Planned: use the icon for the exe and a Linux launcher

## Why

`docs/img/icon.svg` (shield and prompt, transparent; `icon.png` 256 px) exists, but nothing
uses it: `dao-modman.exe` shows PyApp's default icon, and on Linux there is no menu entry at
all (the nxm handler's desktop file is hidden, `NoDisplay=true`).

## The exe (Windows download)

- PyApp has no icon option. After `cargo build`, `packaging/windows/build.ps1` writes the icon
  into the exe with rcedit (https://github.com/electron/rcedit): pinned release, checksum
  checked, like the PyApp source.
- Needs a multi-size `.ico` (16, 24, 32, 48, 64, 256 px): render `icon.svg` to PNGs at those
  sizes and pack them (e.g. Pillow in a `uvx --with pillow` step, or keep a generated
  `packaging/windows/dao-modman.ico` in the repo and rebuild it only when the icon changes).
- Check the small sizes by eye: below 32 px the prompt may need a thicker stroke.

## Linux launcher

- A visible desktop entry, `~/.local/share/applications/dao-modman.desktop`:
  `Name=dao-modman`, `Exec=dao-modman`, `Terminal=true`, `Icon=dao-modman`,
  `Categories=Game;Utility;`.
- The icon in `~/.local/share/icons/hicolor/scalable/apps/dao-modman.svg` (plus PNG sizes if a
  desktop needs them).
- Installed by its own command or together with the nxm handler (`core/nxm_handler.py`), and
  removed by the matching uninstall; the handler's own entry stays hidden.

## Open questions

- Install the launcher automatically (first start, setup wizard) or only on request?
