# dao-modman

Terminal (Textual) mod manager for **Dragon Age: Origins** on Steam/Proton (Linux).

Downloads mods from Nexus Mods or imports local archives (`.dazip`, `.override`, `.zip`,
`.7z`, `.rar`) into a library, lets you enable/disable, group and prioritize them, adjust
which paths get installed, and applies them to the game's user dir inside the Proton prefix:

    steamapps/compatdata/<17450|47810>/pfx/drive_c/users/steamuser/Documents/BioWare/Dragon Age/

## Setup

    uv sync
    echo 'NEXUS_API_TOKEN=...' > .env      # personal API key from nexusmods.com

The token is read from the environment, `./.env`, or `~/.config/dao-modman/.env`.

## Run

    uv run dao-modman                      # TUI
    uv run dao-modman nxm 'nxm://...'      # handle a "Mod Manager Download" link
    uv run dao-modman install-nxm-handler  # register as nxm:// handler

    uv run pytest
    uv run ruff check . && uv run ruff format .

`.rar` archives need `bsdtar`, `7z` or `unrar` on PATH.

## Keys

| Key              | Action                                   |
| ---------------- | ---------------------------------------- |
| `space`          | enable / disable mod                     |
| `K` / `J`        | move mod up / down in the load order     |
| `g`              | set group                                |
| `n`              | add mod from Nexus (URL, id or `nxm://`) |
| `i`              | import a local archive                   |
| `e`              | edit path operations                     |
| `c`              | check Nexus for updates                  |
| `u`              | update the selected mod in place         |
| `a` / `x`        | apply / unapply                          |
| `delete`         | remove mod from the library              |

## Data

    ~/.local/share/dao-modman/
      downloads/            # original archives, never modified
      mods/<id>/
        mod.toml            # metadata: source, version, archive hash, path operations
        files/              # archive extracted as-is
      profile.toml          # load order, enabled flags, groups
      deployed.json         # what was written to the game dir (path + SHA-256)
    ~/.config/dao-modman/config.toml   # optional overrides, see below

`config.toml` keys (all optional): `game_user_dir`, `data_dir`, `downloads_dir`.

## Nexus Mods

Add a mod by URL or mod id, pick a file, and it is downloaded to `downloads/` and imported.
Premium accounts download directly. Free accounts must use the "Mod Manager Download" button
on the website (Nexus API restriction): paste the `nxm://` link into the add dialog or
register dao-modman as the handler. Mod and file ids are stored in `mod.toml`, which enables
update checks. A file that supersedes an installed one replaces it in place, so load order and
path operations carry over. Manually imported archives are looked up on Nexus by MD5.

## Path operations

Archives often contain readmes, screenshots or several variants. Before applying, each mod's
file tree can be edited: **remove** a file/folder or **rename/move** it. The operations are
stored in order in `mod.toml` and replayed when deploying; the extracted files and the archive
are never changed, so operations can be undone at any time.

    [[ops]]
    op = "remove"
    path = "packages/core/override/my-mod/optional"     # file, folder or glob

    [[ops]]
    op = "rename"
    from = "packages/core/override/my-mod/variant-b"
    to = "packages/core/override/my-mod/textures"

Paths are relative to the game user dir and matched case-insensitively.

## How deployment works

- Target paths are relative to the game user dir: dazip `Contents/` maps 1:1; loose files go
  to `packages/core/override/<mod-id>/`. Path operations are then applied on top.
- An archive that wraps a single `.dazip` is unwrapped on import.
- Load order: bottom of the list = highest priority; on identical paths it wins.
- Every deployed file is recorded with its SHA-256. Unapply deletes only files that are still
  unchanged; files that existed before or were edited are left alone.
- dazip AddIns are added to / removed from `Settings/AddIns.xml`.

## Layout

    src/dao_modman/
      core/            # no UI code - testable on its own
        paths.py       # Steam library / Proton prefix / XDG dirs
        config.py      # config.toml, API token
        models.py      # Mod, PathOp, Profile
        metadata.py    # mod.toml / profile.toml
        archive.py     # safe extraction, hashing
        dazip.py       # Manifest.xml parsing
        library.py     # import archives into mods/<id>/
        pathops.py     # remove/rename resolution
        nexus.py       # Nexus API client, nxm:// links, downloads
        install.py     # download + import, in-place updates, MD5 lookup
        addins.py      # Settings/AddIns.xml
        deploy.py      # plan (ops, priority, conflicts), apply, unapply
      tui/
        app.py, app.tcss
        screens.py     # modal dialogs
    tests/
