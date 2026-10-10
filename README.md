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
    uv run dao-modman nxm 'nxm://...'      # hand a "Mod Manager Download" link to the TUI
    uv run dao-modman install-nxm-handler  # register as nxm:// handler
    uv run dao-modman laa status|patch|restore   # Large Address Aware patch, see below
    uv run dao-modman console status|enable|disable   # developer console, see below

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
| `h`              | hide / show the game's official DLC      |
| `t`              | tools (LAA patch, developer console)     |
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

`config.toml` keys (all optional): `game_user_dir`, `game_install_dir`, `data_dir`,
`downloads_dir`, `terminal`.

## Nexus Mods

Add a mod by URL or mod id, pick a file, and it is downloaded to `downloads/` and imported.
Premium accounts download directly. Free accounts must use the "Mod Manager Download" button
on the website (Nexus API restriction): paste the `nxm://` link into the add dialog or
register dao-modman as the handler. Mod and file ids are stored in `mod.toml`, which enables
update checks. A file that supersedes an installed one replaces it in place, so load order and
path operations carry over. Manually imported archives are looked up on Nexus by MD5.

### The nxm:// handler

After `dao-modman install-nxm-handler`, clicking "Mod Manager Download" in the browser hands
the link to the running dao-modman, which shows the download in a panel above the status
line; several downloads can run side by side. If dao-modman is not running, the handler opens
it in a new terminal window and starts the download there.

The running instance listens on a Unix socket in `$XDG_RUNTIME_DIR`, accessible only to your
user. The handler runs outside the project directory, so the API token must be in the
environment or in `~/.config/dao-modman/.env`.

Which terminal is opened: `terminal` in `config.toml` (a command, e.g. `terminal = "kitty"`
or `"kitty --class dao-modman"`), else `$TERMINAL`, else `xdg-terminal-exec`, else KDE's
configured terminal, else the first one found of konsole, gnome-terminal, kitty, alacritty,
foot, wezterm, xterm.

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

In the editor (`e`): `r` removes and `m` renames/moves the selected entry, `u` undoes the last
operation. `←`/`→` fold and unfold a folder, `c` folds or unfolds everything, and `/` filters
the tree to entries whose file or folder name contains the typed text (`esc` clears it).

## How deployment works

- Target paths are relative to the game user dir: dazip `Contents/` maps 1:1; loose files go
  to `packages/core/override/<mod-id>/`. Path operations are then applied on top.
- An archive that wraps a single `.dazip` is unwrapped on import.
- Load order: bottom of the list = highest priority; on identical paths it wins.
- Below an `override` directory the game finds files by name only, in any subfolder. Two mods
  shipping a file with the same name there conflict even at different paths, so only the copy
  of the higher-priority mod is deployed. No need for `zz_` folder prefixes or `toolsetexport`.
  Copies with identical content are not reported as a conflict, since it makes no difference
  which one is used.
- `chargenmorphcfg.xml` is the exception: when several enabled mods ship one, their entries
  are merged into a single `packages/core/override/chargenmorphcfg.xml` (what the
  CharGenMorph Compiler does) and the individual copies are not deployed.
- Every deployed file is recorded with its SHA-256. Unapply deletes only files that are still
  unchanged; files that existed before or were edited are left alone.
- dazip AddIns are added to / removed from `Settings/AddIns.xml`.

## Official DLC and other AddIns

The game's `Settings/addins.xml` already lists its own DLC, and dazip mods are registered in
the same file. Entries that do not belong to a mod in the library are shown read-only above
the mods; `h` hides the official ones. As for mods, `✓` in the `On` column means enabled
(here: in the game), and disabled rows are dimmed.

- **official**: the AddIn ships in the game's install dir (`Addins/<uid>/`), or carries
  `BioWare="1"`. The install dir is the reliable sign; the attribute is self-declared and only
  needed for DLC that was downloaded into the user dir.
- **external**: anything else, i.e. installed by hand or by another mod manager.

An AddIn registered by one of the library's mods is never counted as official, whatever its
manifest claims.

## Large Address Aware (LAA) patch

A heavily modded game runs out of memory unless the executable is Large Address Aware. The
Steam executable cannot simply be flagged in place, so a patched `DAOrigins.exe` is kept in
`src/dao_modman/assets/laa/`. `dao-modman laa patch` (or Tools in the TUI) copies it over
`bin_ship/DAOrigins.exe` in the game's install dir and keeps the original next to it as
`DAOrigins.exe.pre-laa`; `laa restore` puts it back. Steam's "verify integrity of game files"
also restores the original, after which the patch has to be applied again.

## Developer console

`dao-modman console enable` (or Tools in the TUI) switches the game's developer console on,
the way the [Dragon Age wiki](https://dragonage.fandom.com/wiki/Console_(Origins)#Steam)
describes for the Steam release:

- `-enabledeveloperconsole` is added to the entry that launches `DAOrigins.exe` in
  `data/DAOriginsLauncher.xml` of the install dir. The original is kept as
  `DAOriginsLauncher.xml.pre-console`; `console disable` removes the switch again.
- The console opens with the key bound to `OpenConsole_0` in `Settings/KeyBindings.ini`
  (grave by default). If no key is assigned there, grave is bound.

What you type is invisible unless the game has a `packages/core/data/fonts.erf`. If it is
missing, the tool offers to open the Nexus page of
[Make CONSOLE commands visible](https://www.nexusmods.com/dragonage/mods/1110). When that mod
is installed through dao-modman, its `fonts.erf` is routed to `packages/core/data/`
automatically (a rename operation in its `mod.toml`).

## Layout

    src/dao_modman/
      core/            # no UI code - testable on its own
        paths.py       # Steam library / game install dir / Proton prefix / XDG dirs
        config.py      # config.toml, API token
        models.py      # Mod, PathOp, Profile
        metadata.py    # mod.toml / profile.toml
        archive.py     # safe extraction, hashing
        dazip.py       # Manifest.xml parsing
        library.py     # import archives into mods/<id>/
        pathops.py     # remove/rename resolution
        nexus.py       # Nexus API client, nxm:// links, downloads
        ipc.py         # socket the nxm:// handler uses to reach the running instance
        terminal.py    # find a terminal emulator to open a new instance in
        install.py     # download + import, in-place updates, MD5 lookup
        addins.py      # Settings/AddIns.xml
        chargen.py     # chargenmorphcfg.xml merging
        deploy.py      # plan (ops, priority, conflicts, merges), apply, unapply
        laa.py         # LAA patch for the game executable
        console.py     # developer console switch, key binding, fonts.erf
      assets/laa/      # LAA-patched DAOrigins.exe
      tui/
        app.py, app.tcss
        screens.py     # modal dialogs
    tests/

## Further reading

- [Comprehensive Modding Guide for DA:O (Ultimate Edition)](https://steamcommunity.com/sharedfiles/filedetails/?id=3279785773)
  by psousa: general modding advice (override priority, conflicts, `toolsetexport`,
  `chargenmorphcfg.xml`) and a curated mod list.
