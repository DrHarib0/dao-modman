# Planned: shareable profiles

## Why

A mod setup should be shareable: export the load order with each mod's Nexus file and its
choices, import it elsewhere and get the same setup. That needs several profiles: importing one
creates a fresh profile, all or nothing; merging profiles is out of scope. Sharing is limited
to Nexus mods, importing to premium accounts (free accounts would need one browser click per
file, and automating the website is against Nexus' rules).

## Drop revisions

The opt-in git history of the metadata was a debugging aid that was never really used, and
would not survive per-profile files anyway (a profile as a git branch was considered: branches
would swap `deployed.json`, which must match the disk, and leave untracked `mods/*/files/`
behind).

- Delete `core/revisions.py`, `tests/test_revisions.py`.
- `core/library.py:14, 122, 127`: the `snapshot` calls in `save_profile` / `save_mod` (their
  `action` argument then only describes the change; keep or drop).
- `tui/app.py:35, 1064, 1161`: snapshots after apply / unapply.
- `__main__.py:20, 182-191, 217-220, 245-246`: `revisions init`.
- Docs: `README.md:116`, `docs/Readme.md:384-394` ("Library revisions").

## Multiple profiles

- Storage: `Dirs.profile_file` (`core/paths.py:31-33`, fixed `data/profile.toml`) becomes one
  file per profile plus the active profile's name. `metadata.load_profile` / `save_profile`
  (`core/metadata.py:135-164`) keep working per file; `Profile` (`core/models.py:69-87`) may
  get a name. Migration: the existing `profile.toml` becomes the first profile.
- `Library.reload` / `save_profile` (`library.py:103-107, 119-122`) use the active profile;
  add switching.
- Single-profile assumptions to fix:
  - `_sync_profile` (`library.py:109-117`) appends every unknown mod, enabled: with several
    profiles, mods added through another profile would switch on everywhere (append disabled,
    or only where they were added).
  - `import_archive` (`library.py:328`) appends to the active profile only.
  - `remove` (`library.py:353-364`) deletes files other profiles may still list: check or
    update all profile files.
- Deploy is unaffected: one `deployed.json` (`paths.py:35-37`), and `deploy.apply` works from
  the previous record (`core/deploy.py:433`), so switching + apply updates incrementally.

## Ops and options move into the profile

Each profile has its own path operations and package option choices.

- `core/models.py`: `ops`, `options` from `Mod` (57-59) to `ProfileEntry` (62-66).
- `core/metadata.py`: out of `mod_to_dict` / `mod_from_dict` (79-82, 111, 116), into the
  profile file (`op_to_dict` / `op_from_dict` keep the validation). Migration: move them from
  old `mod.toml` files into the entries once (unknown keys are ignored when reading).
- `core/library.py`: `default_targets` (180-186, `mod.options`), `selection` (188-194,
  `mod.ops`), `targets` (196-198), `addins` (208-219) take the entry too; `set_option`
  (200-206) writes the entry and calls `save_profile`.
- Callers, all have the entry at hand: `core/deploy.py:200` (loops `ordered()`, 195),
  `core/moditems.py:126`, `core/report.py:123`, `core/resources.py:51`; `core/adopt.py:182`
  (`_verify`, fresh mod: empty ops/options); `tui/app.py:464, 474` (table counts),
  `tui/app.py:742-751` (ops editor: `entry.ops` + `save_profile`); `tui/screens.py:655`
  (`PathOpsScreen` initial ops), `tui/screens.py:360, 363, 382, 390` (options screen).
- `core/pathops.py:143-145` `resolve(mod, ...)`: only tests use it (`test_pathops.py:154-157`).
- `replace_archive` (`library.py:332-351`) "keeps path ops": now per profile automatically.

## Export (any account)

The active profile in load order: per entry Nexus `game`/`mod_id`/`file_id`, file name, mod
name and version (for display), enabled, group, ops, options; plus `hide_official` if it stays
per profile. Mods without `mod.nexus` are left out and listed; `link_to_nexus`
(`core/install.py:82-97`) can identify manually imported archives by MD5 first. TOML, like
the rest.

## Import into a new profile (premium only)

- Refuse unless `account["is_premium"]` (`tui/app.py:825`); `download_urls` 403s otherwise
  (`core/nexus.py:202-206`).
- Validate: integer ids, game `dragonage`, ops via `op_from_dict`.
- Per entry: reuse the installed mod if `find_nexus(mod_id, file_id)` (`library.py:243-252`),
  else download with `install_from_nexus` (`core/install.py:40-79`). It needs a mode that only
  adds to the library: today it appends to the active profile (via `import_archive`) and
  updates a superseded mod in place (`superseded_mod`, 30-37), which would change other
  profiles: add the file as a separate mod instead.
- All or nothing: download everything first, then write the new profile file (built as a list,
  `Profile.move` only steps by one); on failure no profile.
- Options are stored by label (`"Section/Key"` -> label); `set_option` needs an `OptionKey`
  from `library.options(mod)` (147-167): writing the entry directly is enough, unknown labels
  only cause warnings in `overridepkg.apply_options`.

## TUI

- Footer (`tui/app.py:132-178`, must fit 80 columns, `tests/test_tui.py:1004-1019`): unapply
  (`x`, line 146) leaves the footer (stays on `x` and in `?`); new
  `Key("P", "profiles", "Profiles", "Switch, create, export or import profiles", "App",
  footer=True)` (`p` is Options; free: `b d f j k m w y z`, `P`). "Profiles" is one character
  longer than "Unapply": the test tells.
- Profiles screen: switch, create, rename, delete, export, import. The status line shows the
  active profile.

## Tests

Profile file: `test_metadata.py`, `test_tui.py`, `test_report.py`. Ops/options on `Mod`:
mostly `test_tui.py`, `test_overridepkg.py`, plus `test_deploy`, `test_metadata`,
`test_pathops`, `test_archive_library`, `test_adopt`, `test_nexus`.
