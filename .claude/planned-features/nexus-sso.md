# Planned: log in with the Nexus account (SSO)

## Why

Today every user creates a personal API key on nexusmods.com and pastes it into dao-modman
(settings, setup wizard) or `.nexus-token`. Nexus allows personal keys for apps in testing; the
README promises that before a public announcement dao-modman is registered with Nexus and logs
in with the Nexus account instead.

## What is known

- Needs dao-modman registered as an application with Nexus Mods first (the author's note in
  `nexus-upload.md`: request an API key for SSO). Nexus then gives an application slug; the
  login opens the browser, the user approves, and dao-modman receives the key.
- Where the key is used today: `core/config.py` (`load_token`, `save_token`, `TOKEN_FILE`,
  `NEXUS_API_TOKEN`), `tui/settings.py` (the "Nexus API key" row and wizard step),
  `ModManApp.use_token` (checks the key, re-creates the Nexus client).
- Keep the key in the OS keyring instead of a file: Windows Credential Manager, Secret Service
  (KDE Wallet / GNOME Keyring) on Linux, e.g. with the `keyring` package. `.nexus-token` and
  `NEXUS_API_TOKEN` stay as fallbacks (headless systems, no keyring).
- The settings row and the wizard step become "Log in to Nexus Mods" (and "Log out").

## Open questions

- Nexus' current SSO flow and its terms for registered apps (check when registering).
- What to do with an existing `.nexus-token` once the user has logged in (move into the
  keyring, then delete the file?).
