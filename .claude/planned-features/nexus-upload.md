# Planned: upload the Windows zip to Nexus Mods

## Why

Each release builds `dao-modman-windows.zip` (`.github/workflows/windows-exe.yml`, called from
`publish.yml`) and attaches it to the GitHub release. It should also land, as a new version of
the existing file, on the dao-modman mod page on Nexus, with the release notes as changelog,
so nobody uploads it by hand.

## Upload to Nexus: `Nexus-Mods/upload-action`

- Node 24 action, v3 API; uploads a new version of an existing file. The mod page and a first
  file must exist: they do, the mod is still a draft.
- Pin: newest tag `v1.0.0-beta.11` = `98532f7670c27cbecc78fae03fa3e278147827e2` (10 Sep 2026).
- Inputs:
  - `api_key: ${{ secrets.NEXUSMODS_API_KEY }}`
  - `mod_id: ${{ vars.NEXUS_MOD_ID }}`, `file_id: ${{ vars.NEXUS_FILE_ID }}`
  - `filename: dao-modman-windows.zip`, `version: <tag without v>`
  - `display_name: dao-modman for Windows`, `category: main`
  - `archive_existing_version: true`, `update_mod_version: true`
  - `allow_mod_manager_download: false` (a mod manager is not installed by a mod manager)
  - `changelog: <release notes>` (needs `mod_id`)
- New last job `nexus` in `publish.yml`: `needs: [version, github-release]` (the tag is
  `needs.version.outputs.tag`),
  `if: vars.NEXUS_FILE_ID != ''` (skipped until set up), `environment: nexus`,
  `runs-on: ubuntu-latest`. Downloads the `dao-modman-windows` artifact, reads the notes with
  `gh release view "$TAG" --json body --jq .body` into a multi-line output, runs the action.
- Not documented, answered by the first run: whether a draft mod accepts uploads, and which
  kind of API key is needed (the README only links nexusmods.com/settings/api-keys).

## One-time setup (the author)

1. Nexus: the mod id (mod page URL) and the file id of the Windows download (Files tab →
   "Advanced", or Manage Files → edit).
2. GitHub → Settings → Environments → `nexus`: secret `NEXUSMODS_API_KEY`; optionally required
   reviewers, so each upload waits for a click while the mod is a draft.
3. Repository variables `NEXUS_MOD_ID` and `NEXUS_FILE_ID`.

## Docs

README, Development (release paragraph): the zip also goes to Nexus once `NEXUS_FILE_ID` is set.

## Nexus adaptations

- Separate file for mod description. Need to check the format, it is not markdown.
- Must include link to github repo. The exe is flagged by nexus - user needs to contact support
- mod in draft state: https://www.nexusmods.com/dragonage/mods/7085
- open question: do we need to generate the full changelog for nexus? would be an additional git-cliff step
- more screenshots: tool window, CLI commands, individual tools
- request API key for SSO