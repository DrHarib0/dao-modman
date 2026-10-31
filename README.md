# dao-modman

Terminal based mod manager for 
[**Dragon Age: Origins**](https://store.steampowered.com/app/47810/Dragon_Age_Origins__Ultimate_Edition/) 
on Steam/Proton (Linux), heavily inspired by [MO2](https://www.modorganizer.org/).

![Screenshot of the TUI](https://raw.githubusercontent.com/DrHarib0/dao-modman/main/docs/img/ui-screenshot-1.png)

## Motivation

DA:O is, in the author's opinion, one of the greatest RPGs ever made. It was released in 2009, and is still the best
entry of its series. That is true even if you play the vanilla version, which came with, well, a few bugs.

The computer you are using now is probably very different from the one you used when this game came out. Thankfully, the
modding community for this game is simply amazing.  
Counting total number of mods, DA:O is still in the top 25 as of 2026. 

This program will be the only one to need to use what other fans have made,
and experience the game as it was meant to be - or however you want it to be. 

This is an old game. It was limited to 2GB of RAM, and windows only. It had so many bugs. Over the years, many tools
and mods have addressed those issues. Most of them were targeted for windows and needed specialized tools.
+ you need a lot of mods
+ file structure/overriding
+ special tools. addins, overrides, custom formats like .override

## This tool does it all 

+ the author did not want to run a bunch of tools through wine
+ everything in one place. clean override directory at any time

## Features
- Mod Ordering: no more prefixes for mod directories - load order is defined in the manager
- `dazip` installation: No need to run `daoupdater` or other tools via wine
- Mixed mods with both `addins` and `override` files: these often need manual steps with the Windows-based mod managers.
- Configurable mods: supports the `.override` format introduced by [DAO-Modmanager](https://www.nexusmods.com/dragonage/mods/277)
- `chargenmorphcfg.xml` merging: runs automatically if you add/remove a mod containing character morphs
- Nexus Integration: Download mods from [nexusmods](https://www.nexusmods.com/games/dragonage)
- Archive formats: `.dazip`, `.override`, `.zip`, `.7z` and `.rar`, also nested
- Conflict Detection: see which mod overwrites which, and decide by load order or by leaving single files out
- Mix and Match: pick which files of a mod you want, in the UI; the directory structure is taken care of. 
  This selection persists if you disable a mod and reenable it later.
- Setup: to get the game running smoothly, `dao-modman` handles the technical stuff:
  - LAA Patch so the game can use 4GB of RAM instead of its intended 2GB
  - enabling the developer console
  - scaling down (modded) textures on heavily modded games to fit into the 4GB limit
- Item Browser: list items added by a mod. Automatically writes the item DB for [Add any item visual deluxe](https://www.nexusmods.com/dragonage/mods/1825) 
  when installed, freeing you from yet another windows tool to run manually.

### Install mid-playthrough
+ designed to start from clean slate, but will up mods already installed
+ pre-installed .override packages cannot be reconfigured, must add manually

## Supported platforms

Currently only the Steam release on Linux (Proton) has been tested. Steam installs are found
automatically; for other installs on Linux, e.g. with Lutris or plain Wine, set the game's
directories and launch command in `~/.config/dao-modman/config.toml`, see
[other installs](https://github.com/DrHarib0/dao-modman/blob/main/docs/Readme.md#other-installs-lutris-wine).

Windows and MacOSX are not officially supported yet. 
Porting is not a big deal, but it needs testing: volunteers are welcome.

## Setup

Install from PyPI with [uv](https://docs.astral.sh/uv/getting-started/installation/), or with pipx:

    uv tool install dao-modman             # or: pipx install dao-modman

Alternatively clone this repo and run the application with uv:

    uv run dao-modman

Then store your personal API key from nexusmods.com where dao-modman finds it:

    mkdir -p ~/.config/dao-modman
    touch ~/.config/dao-modman/.nexus-token
    chmod 600 ~/.config/dao-modman/.nexus-token
    # then paste the token into that file, e.g. with your editor

The token is read from the environment variable `NEXUS_API_TOKEN`, else from
`~/.config/dao-modman/.nexus-token` (the token alone). dao-modman warns if other users can
read that file. On Windows the file is `%APPDATA%\dao-modman\.nexus-token`.
Go to [Account Settings/API Keys](https://www.nexusmods.com/settings/api-keys) to create a token. 
Use your own key and never share it.

dao-modman is in beta testing. Nexus Mods allows personal API keys for apps in testing; before
a public announcement, dao-modman will be registered with Nexus Mods and switch to logging in
with your Nexus account (SSO), so you no longer need to create a key.

## Run

To run the mod manager in UI mode, use:

    dao-modman                             # run the TUI

Most functionality is also exposed via a command line interface:

    dao-modman nxm 'nxm://...'             # hand a "Mod Manager Download" link to the TUI
    dao-modman install-nxm-handler         # register as nxm:// handler
    dao-modman laa status|restore|patch --exe PATH   # Large Address Aware patch
    dao-modman console status|enable|disable   # developer console
    dao-modman revisions init              # keep a git history of the library's metadata
    dao-modman chargen                     # check the merged character creator list
    dao-modman conflicts                   # which mods ship the same files, and who wins

`.rar` archives need `unrar`, `7z` or `bsdtar` on PATH; they are tried in that order, since
`bsdtar` cannot read solid archives. On Windows, WinRAR and 7-Zip are also found where they are
installed. Failing all of these, `unrar_command` in `config.toml` sets the command to use, see
the [documentation](https://github.com/DrHarib0/dao-modman/blob/main/docs/Readme.md#rar-archives).
Some mods on Nexus are only available as `.rar`.


## UI Hotkeys

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
| `e`              | choose which files of a mod are installed |
| `o`              | open the mod's Nexus page in the browser |
| `c`              | check Nexus for updates                  |
| `u`              | update the selected mod in place         |
| `v`              | conflicts of the selected mod            |
| `L`              | items the mod adds, by in-game name      |
| `a` / `x`        | apply / unapply                          |
| `l`              | launch the game (through Steam)          |
| `h`              | hide / show the game's official DLC      |
| `/`              | filter by name or group (`esc` clears)   |
| `t`              | tools (LAA, textures, all conflicts ...) |
| `delete`         | remove mod from the library              |


## Usage

See the [user and behind-the-scenes documentation](https://github.com/DrHarib0/dao-modman/blob/main/docs/Readme.md).

## License

[MIT](https://github.com/DrHarib0/dao-modman/blob/main/LICENSE): free to use, change and pass on, as long as the copyright notice naming the author
stays with it.

## Disclosure

Most of the code in this project was co-authored by claude. In-game testing was all done by a human, namely the author of this project.

## Contributing

Testers on other platforms are very welcome: Windows, and the GOG and EA releases of the game. If you
have such a setup and want to help, open an issue and say what you run.

Pull requests are welcome too. Please run the tests and the linter (see below) before sending one.

## Development

To work from source, clone this repository and run `uv sync`; then start it with `uv run dao-modman`.

    uv run pytest                                  # tests
    uv run ruff check . && uv run ruff format .    # lint and format

The version comes from the git tags: a release tag `vX.Y.Z` builds as exactly that version,
later commits as a pre-release of the next one (e.g. `0.1.1.dev4+g7a7a715`).

Releases are started by hand: Actions → Release → Run workflow, or `gh workflow run publish.yml`.
The workflow runs the tests, derives the next version from the [conventional
commits](https://www.conventionalcommits.org) since the last release (`feat:` → minor, anything
else → patch; an explicit version can be given), tags the tested commit, creates the GitHub
release with the changes since the last one as notes, written by [git-cliff](https://git-cliff.org),
and publishes the package to PyPI. There is no changelog file in the repository; `uvx git-cliff`
prints the full changelog from the history.
