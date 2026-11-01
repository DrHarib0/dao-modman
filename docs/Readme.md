# dao-modman documentation

dao-modman keeps mods in its own library and applies the enabled ones to the game's user dir,
which on Steam/Proton is

    steamapps/compatdata/<17450|47810>/pfx/drive_c/users/steamuser/Documents/BioWare/Dragon Age/

(17450 is the original release, 47810 the Ultimate Edition). On Windows it is
`BioWare\Dragon Age` in the user's Documents folder, wherever that is (OneDrive may have moved
it), and Steam's library folders are found through the registry. Either way the dir is found
automatically; `game_user_dir` in `config.toml` overrides it.

## Data

    ~/.local/share/dao-modman/
      downloads/            # original archives, never modified
      mods/<id>/
        mod.toml            # metadata: source, version, archive hash, file choices, options
        files/              # archive extracted as-is
      profile.toml          # load order, enabled flags, groups
      deployed.json         # what was written to the game dir (path + SHA-256)
    ~/.config/dao-modman/config.toml   # optional overrides, see below

On Windows the data is in `%LOCALAPPDATA%\dao-modman\`, and `config.toml` and the API token
are in `%APPDATA%\dao-modman\`; read `~/.config/dao-modman` in this document as the latter.
`XDG_DATA_HOME` and `XDG_CONFIG_HOME` override these folders on every system.

`config.toml` keys (all optional): `game_user_dir`, `game_install_dir`, `game_app_id`,
`launch_command`, `unrar_command`, `data_dir`, `downloads_dir`, `terminal`, `ignore`,
`language`, `max_texture_size`.

## Settings and setup wizard

`s` opens the settings: every `config.toml` key with the value in effect, marked
`(found automatically)` or `(default)` when the file does not set it, plus the Nexus API key,
the nxm:// handler and the setup wizard. `enter` changes the highlighted setting (game folders
are checked: the install folder must hold `bin_ship/DAOrigins.exe`), `delete` puts it back to
its default by removing its line from `config.toml`. Changes are written to `config.toml` line
by line, so your own comments and layout stay; most take effect at once, the library and
downloads folders after a restart. A new API key is checked with Nexus before it is saved.

The setup wizard walks through the game's folders, the Nexus API key, the nxm:// handler and
the game's setup (LAA patch, developer console, a program for `.rar` archives). It starts by
itself on the first start (there is no `config.toml` yet; finishing it creates one) and
whenever the game's folders cannot be found; the settings run it again.

`config.toml` can still be edited by hand. Windows paths go in single quotes there, or use
forward slashes: in double quotes TOML reads `\` as the start of an escape sequence, e.g.
`game_user_dir = 'C:\Users\yourname\Documents\BioWare\Dragon Age'`. The settings screen writes
them that way.

## Updates

At start, dao-modman asks PyPI in the background for the newest release and says so when it is
newer than the running one (offline, it says nothing). "Check for a new version" in the Tools
menu (`t`) asks again and, if there is one, offers to update: dao-modman closes and the update
runs in a new window, since the running program holds its own files. How depends on how it was
installed: `uv tool upgrade dao-modman`, `pipx upgrade dao-modman`, or for the Windows download
`dao-modman.exe self update`; for any other installation it shows the `pip` command. A copy run
from the source tree (a version like `0.5.1.dev2+g1234567`) is never checked: update it with
`git pull`.

The Windows download (`dao-modman-windows.zip` in each release) is a small launcher made with
[PyApp](https://ofek.dev/pyapp/): on its first start it fetches a standalone Python and installs
the newest dao-modman from PyPI into a folder of its own, which `dao-modman.exe self remove`
deletes again. The library and the settings are not in that folder and stay.

## Rar archives

No Python library reads `.rar`, so an external program extracts them: `unrar`, `7z` or
`bsdtar` from PATH, tried in that order (`bsdtar` cannot read solid archives, which are
common). On Windows, where neither installer adds itself to PATH, WinRAR's `UnRAR.exe` and
7-Zip's `7z.exe` are also found through the registry or in `C:\Program Files`. Windows' own
`tar.exe` is not used, since it cannot read solid archives either.

If none of them is there or all fail, `unrar_command` in `config.toml` is tried last: the
program and its arguments, as a list. `{archive}` is replaced by the archive's path, `{dest}`
by the folder to extract into, and the command runs inside that folder:

    unrar_command = ['C:\Tools\unrar.exe', 'x', '-y', '{archive}', '{dest}\']

The command must not write outside `{dest}`; `unrar` and `7z` refuse to by default.

## Other installs (Lutris, Wine)

Only Steam installs are found automatically. For any other install, tell dao-modman where the
game is and how to start it, in `~/.config/dao-modman/config.toml`:

    # inside the Wine prefix: the user's Documents/BioWare/Dragon Age folder
    game_user_dir = "~/Games/dragon-age-origins/drive_c/users/yourname/Documents/BioWare/Dragon Age"
    # the folder with bin_ship/DAOrigins.exe
    game_install_dir = "~/Games/dragon-age-origins/drive_c/GOG Games/Dragon Age Origins"
    # what `l` runs instead of Steam: the program and its arguments, as a list
    launch_command = ["lutris", "lutris:rungame/dragon-age-origins"]

The Lutris game id (`dragon-age-origins` above) is the one `lutris --list-games` shows. A
plain Wine prefix works the same way, e.g.
`launch_command = ["env", "WINEPREFIX=/home/yourname/Games/dao", "wine", "C:\\GOG Games\\Dragon Age Origins\\bin_ship\\DAOrigins.exe"]`.
Only the Steam release has been tested so far.

On Windows the user dir is found for any install (see above), and `l` starts a game that is
not from Steam by running `bin_ship\DAOrigins.exe` in `game_install_dir`, so that setting is
all it needs, e.g. `game_install_dir = 'C:\GOG Games\Dragon Age Origins'`. A Steam game is
started through Steam; `launch_command` overrides both.

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

Adding a file that is in the library already (the same Nexus file, or an archive with the
same content) asks first: from the add dialog, from an `nxm://` link, or when importing an
archive. Yes adds it again as another mod.

### The nxm:// handler

After `dao-modman nxm --install-handler`, clicking "Mod Manager Download" in the browser
hands the link to the running dao-modman, which shows the download in a panel above the
status line; several downloads can run side by side. If dao-modman is not running, the
handler opens it in a new terminal window and starts the download there.
`dao-modman nxm --uninstall-handler` unregisters it again. Do that before uninstalling
dao-modman (`uv tool uninstall dao-modman`, `pipx uninstall dao-modman`): the registration
names the Python of that install, and nxm:// links fail once it is gone. After reinstalling,
or switching to another install, run `--install-handler` again.

On Linux the handler is a desktop file in `~/.local/share/applications`, made the default
with `xdg-mime`. On Windows it is registered for your user only, in
`HKEY_CURRENT_USER\Software\Classes\nxm`, and runs without a console window; problems show up
in a message box. Only one program can handle `nxm://` links: registering dao-modman replaces
Vortex or Mod Organizer (it says so), and unregistering does not bring them back, they
register themselves again from their own settings. A handler that is not dao-modman's is
never removed.

The running instance listens on a port on `localhost`; the port and a random token are in a
file only your user can read, and the handler must present the token. The handler is started
by the browser, which rarely sees variables set in a shell, so the API token is best kept in
`~/.config/dao-modman/.nexus-token`; `--install-handler` says so if it is not there.

Which terminal is opened on Linux: `terminal` in `config.toml` (a command, e.g.
`terminal = "kitty"` or `"kitty --class dao-modman"`), else `$TERMINAL`, else
`xdg-terminal-exec`, else KDE's configured terminal, else the first one found of konsole,
alacritty, xterm, kitty, foot, gnome-terminal, ptyxis, wezterm. On Windows dao-modman gets a console
of its own, which Windows 11 opens in your default terminal (Windows Terminal unless changed);
`terminal` in `config.toml`, e.g. `terminal = "wt"`, is put in front of the command instead.

## Choosing a mod's files

Archives often contain readmes, screenshots, optional parts or several variants. Where a
mod's files end up does not matter: below `override/` the game finds files by name, in any
folder. What matters is *which* files are installed, and that is what the file editor (`e`)
is for. It shows every file of the mod where it would go; files that are not installed are
dimmed and marked "ignored":

- files the [ignore patterns](#ignored-files) leave out (readmes, pictures, links ...),
- files and folders you exclude.

`space` excludes the selected file or folder, or includes it again; that also works for a
file an ignore pattern leaves out, e.g. a readme a mod really needs. `u` undoes the last
change, `o` opens the selected file's copy in the library with the program the desktop uses
for it (for programs and scripts, `.exe`, `.bat`, `.sh` ..., it asks first, as opening them
may run them). `←`/`→` fold and unfold a folder, `c` folds or unfolds everything, and `/`
filters the tree to entries whose file or folder name contains the typed text (`esc` clears
it). `s` saves, `esc` cancels.

The choices are stored as operations in `mod.toml`, applied in order when deploying; the
extracted files and the archive are never changed, so they can be undone at any time:

    [[ops]]
    op = "exclude"
    path = "packages/core/override/my-mod/optional"     # file, folder or glob

    [[ops]]
    op = "include"
    path = "packages/core/override/my-mod/optional/readme.txt"

Paths are relative to the game user dir and matched case-insensitively. The `Ignored` column
of the mod list counts the files of a mod that are not installed.

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
  to `packages/core/override/<mod-id>/`. The ignore patterns and the mod's exclude and
  include operations then decide which of them are installed.
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
- **Files are linked instead of copied where possible.** On Linux they are cloned when the
  library and the game are on the same filesystem with copy-on-write support (btrfs, XFS,
  bcachefs …): applying is quick and takes no extra space, and the game's copy is still a
  file of its own. On Windows they are hard links when both are on the same drive (NTFS).
  Anything else is copied, as are merged files and textures reduced to the size limit; the
  result says how many files were linked.
- **A hard-linked file is the library's file too.** A tool that edits an applied file in
  place on Windows changes the library's copy with it. Apply and unapply notice this and
  offer to restore the mod from its archive in `downloads/` (if it is still there and
  unchanged); after that, apply can also put the original back into the game, which
  discards the changes made to it. A tool that replaces the file instead leaves the library
  alone, like any file changed by hand.
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

A conflict is one mod overwriting a file of another: when several enabled mods ship a file
(the same path, or the same name below an override directory), the game gets the copy of
the mod that loads last. The load order decides, nothing else:

- to change which copy wins, move the mods (`J`/`K`);
- to keep a single file of a mod out of the game, exclude it in the mod's file dialog (`e`),
  and the other mod's copy is used.

The `Notes` column of the mod list shows the direction: `overwrites <mod>` on the winner,
`overwritten by <mod>` on the others (the number of mods, if several).

`v` in the TUI shows the conflicts the selected mod takes part in; "Show all conflicts" in
the Tools menu (`t`), or `dao-modman conflicts`, shows them for the whole library. The
report lists every contested file grouped by the mods that ship it. Mods are shown in load
order with the winner last; under each file is every copy with its folder, and the one the
game gets is marked:

    #4 Dain's Fixes  <  #5 Extras   (1 file)
      abi_bloodcontrol.gda
        #4 Dain's Fixes   packages/core/override/dain-s-fixes/2. Tweaks/Blood Control/
        #5 Extras         packages/core/override/extras/Blood Control damage on resist/   ← used

Files in the mod directories that dao-modman did not install take part too, as "not managed
by dao-modman", listed last: apply never replaces them, so they always win (`overwritten by
unmanaged files`). Adopt them (see below) to put them in the load order.

## Taking over an existing installation

If the game was modded before dao-modman, the mod directories (`packages/` and `AddIns/` in the
user dir) hold files it does not manage. dao-modman says so at startup, never overwrites
them (apply names the files it left alone), and lists them in the conflict report.

"Adopt files not managed by dao-modman" in the Tools menu (`t`) takes them into the library:

- each folder in `packages/core/override/` becomes one mod of loose override files, named
  after the folder; the files directly in `override/` one more. Like any mod, they are
  installed to the mod's own folder in `override/`, which the game does not mind;
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
Steam executable carries DRM that breaks when its header changes, so it cannot simply be
flagged in place, and dao-modman does not ship an executable:
[this guide](https://steamcommunity.com/sharedfiles/filedetails/?id=233222451) explains how
to get a DRM-free or already patched one. The LAA entry in Tools opens the guide in the
browser and asks for the path of that `DAOrigins.exe`; on the command line it is
`dao-modman laa patch --exe PATH`. A DRM-free executable without the flag is flagged on the
way in (the file itself is left as it is); one with Steam's DRM is refused.
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
