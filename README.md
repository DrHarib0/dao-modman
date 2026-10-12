# dao-modman

Terminal (Textual) mod manager for **Dragon Age: Origins** on Steam/Proton (Linux).

Downloads mods from Nexus Mods or imports local archives (`.dazip`, `.override`, `.zip`,
`.7z`, `.rar`; dazips, override packages and loose files) into a library, lets you enable/disable, group and prioritize them, adjust
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
    uv run dao-modman revisions init       # keep a git history of the library's metadata
    uv run dao-modman chargen              # check the merged character creator list
    uv run dao-modman conflicts            # which mods ship the same files, and who wins
    uv run dao-modman conflicts accept NAME ... | --all   # mark conflicts as intended

    uv run pytest
    uv run ruff check . && uv run ruff format .

`.rar` archives need `unrar`, `7z` or `bsdtar` on PATH; they are tried in that order, since
`bsdtar` cannot read solid archives.

## Keys

The bar at the bottom shows the everyday keys; `?` lists all of them.

| Key              | Action                                   |
| ---------------- | ---------------------------------------- |
| `space`          | enable / disable mod                     |
| `K` / `J`        | move mod up / down in the load order     |
| `r`              | rename mod (display name only)           |
| `g`              | set group                                |
| `p`              | options of an override package           |
| `n`              | add mod from Nexus (URL, id or `nxm://`) |
| `i`              | import a local archive                   |
| `e`              | edit path operations                     |
| `o`              | open the mod's Nexus page in the browser |
| `c`              | check Nexus for updates                  |
| `u`              | update the selected mod in place         |
| `v`              | conflict report                          |
| `a` / `x`        | apply / unapply                          |
| `l`              | launch the game through Steam            |
| `h`              | hide / show the game's official DLC      |
| `t`              | tools (LAA, console, creator report)     |
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
    ~/.config/dao-modman/presets.toml  # optional: your own predefined path operations

`config.toml` keys (all optional): `game_user_dir`, `game_install_dir`, `game_app_id`,
`data_dir`, `downloads_dir`, `terminal`, `ignore`.

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

### Predefined path operations

Some mods only work when installed at a specific place. For those, dao-modman knows the
needed operations: when such a mod is installed it shows why and what, and asks whether to
apply them. If you decline, or the mod was installed earlier, the path editor (`e`) shows a
hint and `p` adds them there.

The list is `src/dao_modman/assets/presets.toml`, keyed by Nexus mod id and file id:

    [[preset]]
    mod_id = 1825
    file_id = 9280
    description = "The bundled exe has hardcoded paths. For the mod to work, the exe needs to be placed in override/siramods_items"

    [[preset.ops]]
    op = "rename"
    from = "{mod}/ADD_ITEM_DELUXE_1_1/siramods_items"
    to = "packages/core/override/siramods_items"

`{mod}` stands for the mod's own override folder, `packages/core/override/<mod-id>`, which
differs from library to library. A `presets.toml` of the same format in
`~/.config/dao-modman/` adds your own entries; an entry there replaces the shipped one for
the same mod and file. Problems in that file are reported at startup and it is then ignored.

### Item tables of "Add Any Item Visual Deluxe"

[Nexus mod 1825](https://www.nexusmods.com/dragonage/mods/1825) adds the console scripts
`get` and `getx`, which read two tables of item files, `itlst_biow.gda` (the game's items)
and `itlst_user.gda` (those of mods). The mod ships a Windows scanner to fill them, to be
run again after every change. That is not needed here: when the mod is enabled, every apply
writes both tables itself, from the game's archives and from the mod files being deployed
(plus item files installed by hand). The game table is the same, byte for byte, as the
scanner's; like the scanner it leaves out the encrypted DLC archives.

## Ignored files

The game only loads its own resource types. Readmes, pictures, links and similar files that
mods ship next to them are **ignored**: they stay in the library but are never deployed, do
not show in the path editor, cannot conflict, and do not make a dazip mod `mixed`.

Which files are ignored is set with `ignore` in `config.toml`, a list of patterns in
`.gitignore` syntax. They are matched against paths inside each mod's files, ignoring case:

    ignore = [
        "*.txt",        # any file with this extension, in any folder
        "*readme*",     # any file or folder with "readme" in its name
        "/docs/",       # the docs folder at the top of a mod
        "!keep.txt",    # ... but not this one
    ]

Without the key, a built-in list is used: text (`*.txt`, `*.pdf`, `*.rtf`, `*.doc`, `*.docx`,
`*.odt`, `*.md`, `*.htm`, `*.html`, `*.nfo`, `*.log`), links (`*.url`, `*.lnk`, `*.webloc`),
pictures (`*.jpg`, `*.jpeg`, `*.png`, `*.gif`, `*.bmp`, `*.webp`), `*readme*`, and packer
leftovers (`Thumbs.db`, `desktop.ini`, `.DS_Store`, `__MACOSX/`). Setting `ignore` replaces
that list; `ignore = []` deploys everything. Game formats such as `.dds`, `.tga` and `.xml`
are not on it.

## How deployment works

- Target paths are relative to the game user dir: dazip `Contents/` maps 1:1; loose files go
  to `packages/core/override/<mod-id>/`. Path operations are then applied on top.
- An archive may contain any number of `.dazip` files next to loose files. Each dazip is
  unpacked on import and installed as described by its `Contents/` folder; the rest is treated
  as loose override files. An AddIn is registered as long as at least one file of its dazip is
  still deployed, so removing a module's paths also drops its AddIn.
- The `Kind` column says what a mod consists of: `dazip`, `package` (override package, see
  below), `override` (loose files only) or `mixed` (more than one of these; ignored files do
  not count).
- Load order: bottom of the list = highest priority; on identical paths it wins.
- Below an `override` directory the game finds files by name only, in any subfolder. Two mods
  shipping a file with the same name there conflict even at different paths, so only the copy
  of the higher-priority mod is deployed. No need for `zz_` folder prefixes or `toolsetexport`.
  Copies with identical content are not reported as a conflict, since it makes no difference
  which one is used.
- `chargenmorphcfg.xml` is the exception: when several enabled mods ship one, their entries
  are merged into a single `packages/core/override/chargenmorphcfg.xml` (what the
  CharGenMorph Compiler does) and the individual copies are not deployed.
- Every deployed file is recorded with its SHA-256. A file that already exists in the game
  directory is never overwritten; apply reports how many it left untouched.
- **Apply is all or nothing.** Before copying, it notes what it is about to create. If it
  fails, everything from that run is removed again and nothing is applied; if the process is
  killed, the next start rolls the unfinished run back the same way. (Apply first removes the
  previous deployment, so after a failure that one is gone too.) Apply and unapply show a
  progress dialog and cannot be interrupted from the keyboard.
- **Unapply** removes the files it applied that are still unchanged. If the mod directories
  (`packages/` and `AddIns/` in the game's user dir) contain anything else, such as files
  changed since, files installed by hand or by another tool, or AddIns those registered, it
  asks: `y` removes everything (cannot be undone), `o` only dao-modman's files, `n` cancels.
  Official DLC stored there is never touched.
- dazip AddIns are added to / removed from `Settings/AddIns.xml`.

### Override packages

DAO-Modmanager introduced a third, unofficial format: the override package, a zip with the
extension `.override`. It holds a `Manifest.xml` (name, version, author) and its files below
`BioWare/Dragon Age/`, i.e. with their full path under the Documents folder. Packages are
unpacked on import, also when wrapped in another archive, and installed at the paths they
name; the manifest is never deployed.

A package may offer **options** in an `OverrideConfig.xml`: for one game file, several
variant files of which one is used. `p` lists them, each with its chosen value and the
variant file behind it; `enter` picks another value. Choices are stored in `mod.toml`:

    [options]
    "Human Females/Hand Size" = "NB big"

The chosen variant is deployed under the original file's name, and the other variants are
not deployed at all, so the game dir holds only what is in use. Options are resolved before
path operations: an operation on the original's path applies to whichever variant is chosen,
and changing a choice never touches your operations. In the path editor, a file that comes
from a variant shows it (`hf_glv_nud_0.msh  ← (NB100 hand)hf_glv_nud_0.msh`). Mods with
options show `options` in `Notes`, with the number of choices that differ from the defaults.

Not supported: creating packages, and DAO-Modmanager's separate `DA_ini_config.xml` system.

### Character creator report

The merge of `chargenmorphcfg.xml` happens on every apply. `dao-modman chargen`, or Tools in
the TUI, shows what it amounts to without applying anything: which mods contribute, how many
entries each section holds and who added them, and any file that could not be read. The game's
own entries are told apart by reading the unmodded file from the game's archives.

It also lists entries that **point at nothing**: a hairstyle, colour or head named in a mod's
list for which neither the game's archives nor any enabled mod has a file. Those are the
broken choices in the character creator, typically a typo in a mod or a mod that was switched
off while another still lists its content. The command exits with status 1 when it finds any.

### Conflict report

`v` in the TUI, or `dao-modman conflicts`, lists every contested file grouped by the mods that
ship it. Mods are shown in load order with the winner last; under each file is every copy
with its folder, and the one the game gets is marked:

    #4 Dain's Fixes  <  #5 Extras   (1 file)
      abi_bloodcontrol.gda
        #4 Dain's Fixes   packages/core/override/Dains Fixes/2. Tweaks/Blood Control/
        #5 Extras         packages/core/override/Dains Fixes/6. Extras/Blood Control damage on resist/   ← used

### Accepted overwrites

Some conflicts are intended: one mod is meant to replace files of another. The winning mod
can declare that in its `mod.toml`:

    [[overwrites]]
    mod = "dain-s-fixes"              # id of the mod whose files may be overwritten
    files = ["abi_coneofcold.gda"]    # names or globs, ignoring case; leave out for every file

A conflict is **accepted** when the winner covers every other mod that ships the file; it
then no longer shows as `conflict`. A mod with such rules shows `overwrites <mod>` in the
`Notes` column (or the number of mods, if several). If the load order changes so that the declaring mod loads
*before* the other one, the overwrite does not happen: the mods are marked `wrong order` in
red, the report says so, and `dao-modman conflicts` exits with status 1.

To set it:

- In the conflict report (`v`), `a` on a file line accepts that file; on a mods line it
  accepts everything between those mods, including clashes that appear later. `a` again
  takes it back.
- `dao-modman conflicts accept NAME ...` takes file names (globs allowed) or mod ids; a mod id
  accepts every open conflict that mod takes part in. `dao-modman conflicts accept --all`
  accepts every open conflict. Entries are written file by file.

A rule is kept when the mod it names is removed, since removal is often temporary. It then
does nothing, and is listed in the report as an unused rule so that it does not come back
unnoticed when the mod is installed again; `a` on it in the dialog drops it.

## Library revisions

A debugging aid: `dao-modman revisions init` turns the data directory into a git repository.
From then on every change to the library's metadata is committed there with the action as
its message ("disable some-mod", "bodies: set option Humans/Body = Big", "apply: 2862
files"), so it is possible to see afterwards what changed when:

    git -C ~/.local/share/dao-modman log --stat

Only `mod.toml` files, `profile.toml` and `deployed.json` are tracked, never archives or
extracted mod files. There is no restore. To stop, delete the `.git` folder there.

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
is installed through dao-modman, it offers the predefined path operation that routes its
`fonts.erf` to `packages/core/data/`.

## Layout

    src/dao_modman/
      core/            # no UI code - testable on its own
        paths.py       # Steam library / game install dir / Proton prefix / XDG dirs
        config.py      # config.toml, API token
        models.py      # Mod, PathOp, Profile
        metadata.py    # mod.toml / profile.toml
        archive.py     # safe extraction, hashing
        dazip.py       # Manifest.xml parsing
        overridepkg.py # override packages (.override) and their options
        library.py     # import archives into mods/<id>/
        pathops.py     # remove/rename resolution
        presets.py     # predefined path operations for known mods
        ignore.py      # .gitignore-style rules for files that are never deployed
        nexus.py       # Nexus API client, nxm:// links, downloads
        ipc.py         # socket the nxm:// handler uses to reach the running instance
        terminal.py    # find a terminal emulator to open a new instance in
        install.py     # download + import, in-place updates, MD5 lookup
        addins.py      # Settings/AddIns.xml
        chargen.py     # chargenmorphcfg.xml merging and its report
        itemlists.py   # item tables for the Add Any Item mod, written at apply
        erf.py         # reader for the game's ERF archives
        resources.py   # which resources the game and the enabled mods provide
        deploy.py      # plan (ops, priority, conflicts, merges), apply, unapply, clean
        revisions.py   # git history of the library's metadata
        report.py      # conflict report
        laa.py         # LAA patch for the game executable
        launch.py      # start the game through Steam
        console.py     # developer console switch, key binding, fonts.erf
      assets/laa/      # LAA-patched DAOrigins.exe
      assets/presets.toml   # predefined path operations
      tui/
        app.py, app.tcss
        screens.py     # modal dialogs
    tests/

## Further reading

- [Comprehensive Modding Guide for DA:O (Ultimate Edition)](https://steamcommunity.com/sharedfiles/filedetails/?id=3279785773)
  by psousa: general modding advice (override priority, conflicts, `toolsetexport`,
  `chargenmorphcfg.xml`) and a curated mod list.
