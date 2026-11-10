# Codex projects missing after switching accounts? (2026-11-10)

Codex Account Project Sync is a manual Windows utility for preserving **existing local project grouping and thread membership** across saved Codex accounts. It addresses the case where projects disappear or lose grouping after an account switch, then reappear when switching back.

[Download EXE and source](https://github.com/pancake-github/codex-account-project-sync/releases) · [中文说明](README.md) · [Validation boundaries](docs/validation.md)

First release: **v0.1.0-beta.1**. Target: Windows 11 x64. The supported local account and paginated-history structures were observed in the Codex 26.1103 series. Unknown structures stop writes. This is an independent community utility.

## Workflow

1. Finish the current task and completely quit Codex.
2. Extract the Windows release and double-click `CodexAccountProjectSync.exe`. No Python installation is required.
3. Scan the local data directory, identify account partitions by short IDs and optional local aliases, and select one source plus one or more targets.
4. Preview project and membership counts for every selected account. Review source-preferred conflicts; explicitly resolve target-only conflicts. Confirm the displayed accounts and counts.
5. Wait for backup and synchronization to finish, reopen Codex, and check the target account's grouping and existing chats.

The source also receives projects unique to selected targets. Unselected accounts retain their original settings. The utility preserves native project IDs and does not merge different IDs merely because folders match.

## Data handling

- Discovers accounts already saved in the selected current-user data directory. Does not infer a person's identity, read login tokens for email, or scan other Windows users.
- Uses `CODEX_HOME`, otherwise the current user's `.codex` directory; a custom directory can be selected.
- Writes only `.codex-global-state.json` and its `.bak`. Chat bodies, working files and the official application are not edited.
- Backs up the exact configuration before each modification; the first operation also backs up supported SQLite databases, including committed WAL transactions.
- Keeps aliases, backups and receipts under the current user's local application-data directory, separated by Codex data directory.
- Requires explicit confirmation and a fresh configuration fingerprint. Active Codex processes, unresolved conflicts, unknown schemas or invalid internal mappings block writes.
- Restores the configuration before the latest successful synchronization; it does not overwrite newer chat bodies or working files.

Restore replaces both configuration files with their exact pre-sync bytes. Grouping and interface-preference changes made since that backup are therefore rolled back; the confirmation dialog describes this effect.

The application operates locally without network requests, background persistence or startup tasks. Account/project counts are discovered rather than hard-coded. Already-deleted chats, unsaved accounts and ChatGPT cloud projects are outside this local classification workflow. A membership record count is not a claim that the same number of chats can be recovered.

The GUI currently uses Chinese labels; this guide describes the same actions. Source execution requires Python 3.10+ with Tkinter and only the standard library. The Windows executable is built separately by GitHub Actions. See [the Chinese README](README.md) for source commands and [validation boundaries](docs/validation.md) for measured checks and unverified environments.
