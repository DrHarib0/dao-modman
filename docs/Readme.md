# dao-modman documentation

dao-modman keeps mods in its own library and applies the enabled ones to the game's user dir,
which on Steam/Proton is

    steamapps/compatdata/<17450|47810>/pfx/drive_c/users/steamuser/Documents/BioWare/Dragon Age/

(17450 is the original release, 47810 the Ultimate Edition). It is found automatically;
`game_user_dir` in `config.toml` overrides it.

## Data

    ~/.local/share/dao-modman/
      downloads/            # original archives, never modified
      mods/<id>/
        mod.toml            # metadata: source, version, archive hash, path operations, options
        files/              # archive extracted as-is
      profile.toml          # load order, enabled flags, groups
      deployed.json         # what was written to the game dir (path + SHA-256)
    ~/.config/dao-modman/config.toml   # optional overrides, see below

`config.toml` keys (all optional): `game_user_dir`, `game_install_dir`, `game_app_id`,
`data_dir`, `downloads_dir`, `terminal`, `ignore`, `language`, `max_texture_size`.

## Nexus Mods

Add a mod by URL or mod id (`n`), pick its files, and each is downloaded to `downloads/` and
imported as a mod of its own. `space` selects files, `enter` downloads the selected ones (or
the highlighted one if none is selected). If the mod has exactly one main file, it is
selected already; files that are in the library already are marked "installed".
Premium accounts download directly. Free accounts must use the "Mod Manager Download" button
on the website (Nexus API restriction): selected files open their download pages in the
browser, where "Slow download" hands the link to dao-modman if it is registered as the
handler; an `nxm://` link can also be pasted into the add dialog. Mod and file ids are stored in `mod.toml`, which enables
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

Ignored files (readmes, screenshots, see [Ignored files](#ignored-files)) are shown too,
dimmed and marked "ignored", where they would go: they move along with path operations but
are never installed. `o` opens the selected file's copy in the library with the program the
desktop uses for it; for programs and scripts (`.exe`, `.bat`, `.sh` ...) it asks first, as
opening them may run them.

## Items

### Item Browser

`L` lists the items the selected mod adds, by the names they have in game (what `getx`
filters by) with the kind of item, e.g. Longsword or Helmet; `f` shows their file names
next to them. Items that only replace one of the
game's own are counted, not listed. Names are read from the talk tables of the language set
with `language` in `config.toml` (default `"en-us"`): the game's, the enabled mods', and
the mod's own.

### Item tables of "Add Any Item Visual Deluxe"

[Nexus mod 1825](https://www.nexusmods.com/dragonage/mods/1825) adds the console scripts
`get` and `getx`, which read two tables of item files, `itlst_biow.gda` (the game's items)
and `itlst_user.gda` (those of mods). The mod ships a Windows scanner to fill them, to be
run again after every change. That is not needed here: when the mod is enabled, every apply
writes both tables itself, from the game's archives and from the mod files being deployed
(plus item files installed by hand). The game table starts with exactly the scanner's rows;
the items of the encrypted DLC archives and of the offers, which the scanner cannot see,
follow.

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
- Every deployed file is recorded with its SHA-256, size and time, and with what it was
  made from (the mod's file, or the merged content). A file that already exists in the game
  directory is never overwritten; apply names the ones it left untouched.
- **Apply only changes what differs** from the last apply. Files still wanted as they are
  stay untouched; files no longer wanted, or wanted differently (another mod wins, a path
  operation moved them, the mod was updated, another texture size limit), are removed; new
  ones are written. Enabling one mod copies just its files. A deployed file changed by hand
  since is left alone and no longer counts as dao-modman's. The apply dialog and the result
  say how many files are written, removed and unchanged. A record from a version before
  this is checked by content on the first apply, which reads every applied file once.
- Once something is applied, the Notes column says what apply would do for a mod: **not
  applied**, **changed** (its files differ from what is applied) or **still applied** (it is
  disabled, but its files are in the game); the status line counts these mods.
- **Apply is all or nothing.** Before changing anything, it notes what it is about to do. If
  it fails, every file of dao-modman's is removed and nothing is applied (files changed by
  hand and files of others are never touched); if the process is killed, the next start
  rolls the unfinished run back the same way. Apply and unapply show a progress dialog and
  cannot be interrupted from the keyboard.
- **Unapply** removes the files it applied that are still unchanged. If the mod directories
  (`packages/` and `AddIns/` in the game's user dir) contain anything else, such as files
  changed since, files installed by hand or by another tool, or AddIns those registered, it
  asks: `y` removes everything (cannot be undone), `o` only dao-modman's files, `n` cancels.
  Official DLC stored there is never touched.
- dazip AddIns are added to `Settings/AddIns.xml`, and removed when their mod is disabled
  or unapplied.

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

### Texture size

The game is a 32-bit program and runs out of memory when a texture pack brings very large
textures: loading a save then crashes. `max_texture_size` in `config.toml` sets the longest
side, in pixels, a deployed texture may have (default `4096`; `0` for no limit; a power of
two). The Tools menu (`t`) sets it to no limit, 1K, 2K or 4K. Larger textures are written to the game folder without their largest mipmap levels,
so nothing is compressed again and the library keeps the originals; change the value and
apply again to try another. Textures that cannot be reduced this way (no mipmaps, an
unusual format) are deployed as they are and reported; textures inside `.erf` archives are
not touched.

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

`v` in the TUI shows the conflicts the selected mod takes part in; "Show all conflicts" in
the Tools menu (`t`), or `dao-modman conflicts`, shows them for the whole library. The
report lists every contested file grouped by the mods that ship it. Mods are shown in load order with the winner last; under each file is every copy
with its folder, and the one the game gets is marked:

    #4 Dain's Fixes  <  #5 Extras   (1 file)
      abi_bloodcontrol.gda
        #4 Dain's Fixes   packages/core/override/Dains Fixes/2. Tweaks/Blood Control/
        #5 Extras         packages/core/override/Dains Fixes/6. Extras/Blood Control damage on resist/   ← used

Files in the mod directories that dao-modman did not install take part too, as "not managed
by dao-modman", listed last: apply never replaces them. Such conflicts cannot be accepted;
adopt the files (see below) to put them in the load order.

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

- In the conflict report (`v`, or the Tools menu), `a` on a file line accepts that file; on a mods line it
  accepts everything between those mods, including clashes that appear later. `a` again
  takes it back.
- `dao-modman conflicts accept NAME ...` takes file names (globs allowed) or mod ids; a mod id
  accepts every open conflict that mod takes part in. `dao-modman conflicts accept --all`
  accepts every open conflict. Entries are written file by file.

A rule is kept when the mod it names is removed, since removal is often temporary. It then
does nothing, and is listed in the report as an unused rule so that it does not come back
unnoticed when the mod is installed again; `a` on it in the dialog drops it.

## Taking over an existing installation

If the game was modded before dao-modman, the mod directories (`packages/` and `AddIns/` in the
user dir) hold files it does not manage. dao-modman says so at startup, never overwrites
them (apply names the files it left alone), and lists them in the conflict report.

"Adopt files not managed by dao-modman" in the Tools menu (`t`) takes them into the library:

- each folder in `packages/core/override/` becomes one mod of loose override files, named
  after the folder; the files directly in `override/` one more. If the folder is not named
  like the mod's id, a rename path operation (`e`) keeps the files in their folder;
- each `AddIns/<uid>/` folder becomes one mod. If the AddIn is registered in
  `Settings/AddIns.xml`, it becomes a dazip, named after the AddIn's title, and apply
  registers it again;
- anything else below `packages/` becomes one mod "Other packages files". This one, and
  AddIn folders that are not registered, become override packages: the only layout that
  places files outside the override folder.

Every group is packed into `downloads/adopted-<name>.zip` and imported like any archive; its
files deploy to exactly where they were. The new mods go to the end of the load order, so
they keep winning wherever they did before. Only when every group is imported and every
library copy matches its original are the originals removed and the AddIns unregistered; if
anything fails, the new mods are removed again and the game dir is left as it was. The game
then lacks those files until the next apply, which the TUI offers right away.

Ignored files (readmes, `Thumbs.db` ...) inside an adopted folder go into the library with
it, like any mod's readme, and are not installed again. Other ignored files and symbolic links
are left where they are, and do not trigger the startup notice. Official DLC is never touched. `AddIns.xml` does not record which AddIns came from one dazip, so a mod made
of several AddIns, like The Winter Forge, becomes several mods.

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
Steam executable cannot simply be flagged in place, and dao-modman does not ship a patched
one: [this guide](https://steamcommunity.com/sharedfiles/filedetails/?id=233222451) explains
how to get it. The LAA entry in Tools opens the guide in the browser and asks for the path of
your patched `DAOrigins.exe`; on the command line it is `dao-modman laa patch --exe PATH`.
The file is copied over `bin_ship/DAOrigins.exe` in the game's install dir, and the original
is kept next to it as `DAOrigins.exe.pre-laa`; `laa restore` puts it back. Steam's "verify integrity of game files"
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

What you type is invisible unless the game has a `fonts.erf`, from
[Make CONSOLE commands visible](https://www.nexusmods.com/dragonage/mods/1110). If neither that
mod nor a `fonts.erf` in `packages/core/data` or the override folder is found, the tool offers
to open the mod's Nexus page. Installed like any other mod, its `fonts.erf` works from the
mod's own override folder.

## Further reading

- [Comprehensive Modding Guide for DA:O (Ultimate Edition)](https://steamcommunity.com/sharedfiles/filedetails/?id=3279785773)
  by psousa: general modding advice (override priority, conflicts, `toolsetexport`,
  `chargenmorphcfg.xml`) and a curated mod list.
