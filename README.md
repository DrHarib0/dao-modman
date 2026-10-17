# dao-modman

Terminal based mod manager for 
[**Dragon Age: Origins**](https://store.steampowered.com/app/47810/Dragon_Age_Origins__Ultimate_Edition/) 
on Steam/Proton (Linux), heavily inspired by [MO2](https://www.modorganizer.org/).

![Screenshot of the TUI](https://raw.githubusercontent.com/DrHarib0/dao-modman/main/docs/img/ui-screenshot-1.png)

## A tribute to a masterpiece

DA:O is, in the author's opinion, one of the greatest RPGs ever made. It was released in 2009, more than 15 years ago, and 
is still the best entry of its series. While the writing has aged perfectly, the graphics have not, and the mechanics
only in part. Combat is still fun, especially at higher difficulty levels.

## Motivation

The computer you are running this on is probably very different from the one you used when this game came out. If you
have moved on from Windows to Linux since then like the author did, the goal of this project is to be the only tool you need.

## Features
- Nexus Integration: Download mods from [nexusmods](https://www.nexusmods.com/games/dragonage)
- Archive formats: `.dazip`, `.override`, `.zip`, `.7z` and `.rar`, also nested
- Conflict Detection and Resolving
- Adopt: take over a game that was modded by hand or with another tool
- Mod Ordering: no more prefixes for mod directories - load order is defined in the manager
- Mix and Match: pick which files of a mod you want, in the UI; the directory structure is taken care of. This selection persists if you disable a mod and reenable it later
- Setup: to get the game running smoothly, `dao-modman` handles the technical stuff:
  - LAA Patch so the game can use 4GB of RAM instead of its intended 2GB
  - enabling the developer console
  - scaling down (modded) textures on heavily modded games to fit into the 4GB limit
- `dazip` installation: No need to run `daoupdater` or other tools via wine
- `chargenmorphcfg.xml` merging: runs automatically if you add/remove a mod containing character morphs
- Mixed mods with both `addins` and `override` files: these often need manual steps with the Windows-based mod managers.
  Some very popular mods have a lot of posts about this problem, like the [Winter Forge](https://www.nexusmods.com/dragonage/mods/122).
- Configurable mods: supports the `.override` format introduced by [DAO-Modmanager](https://www.nexusmods.com/dragonage/mods/277)
- Item Browser: list items added by a mod. Automatically writes the item DB for [Add any item visual deluxe](https://www.nexusmods.com/dragonage/mods/1825) 
  when installed, freeing you from yet another windows tool to run manually.

## Supported platforms

Currently only the Steam release on Linux (Proton). Windows and the GOG/EA releases are not supported
yet. Porting is not a big deal, but it needs testing on those setups: volunteers are welcome.

## Setup

Install it from PyPI with [uv](https://docs.astral.sh/uv/getting-started/installation/), or with pipx:

    uv tool install dao-modman             # or: pipx install dao-modman

Then store your personal API key from nexusmods.com where dao-modman finds it:

    mkdir -p ~/.config/dao-modman
    echo 'NEXUS_API_TOKEN=...' > ~/.config/dao-modman/.env
    chmod 600 ~/.config/dao-modman/.env

The token is read from the environment, `./.env`, or `~/.config/dao-modman/.env`.
Go to [Account Settings/API Keys](https://www.nexusmods.com/settings/api-keys) to create a token. 
The usual security recommendations apply. If this gets more than 10 users, the author will apply for an app-specific API key.

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
    dao-modman conflicts accept NAME ... | --all   # mark conflicts as intended

`.rar` archives need `unrar`, `7z` or `bsdtar` on PATH; they are tried in that order, since
`bsdtar` cannot read solid archives. Some mods on Nexus are only available as `.rar`.


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
| `l`              | launch the game through Steam            |
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
