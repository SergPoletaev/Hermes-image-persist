# Release notes — v1.0.1

Text below is ready to copy into GitHub: the repository **About** description
and the **Release** description for the `v1.0.1` tag (attach
`hermes-image-persist-1.0.1.zip` to the release).

---

## Repository description (Settings → General → About)

**English (recommended):**

> Cross-platform fix for hermes-agent #142500: pasted images no longer vanish from the Hermes Desktop chat history in Text/Auto modes — and the fix survives Hermes updates. Windows / Linux / macOS, pure stdlib, full rollback.

Shorter variant:

> Keeps pasted images in Hermes Desktop chat history (hermes-agent #142500). Update-proof, cross-platform, MIT.

**Русский (если нужен):**

> Кроссплатформенный фикc для hermes-agent #142500: вставленные картинки больше не пропадают из истории чата Hermes Desktop в режимах Text/Auto — и фикс переживает обновления Hermes. Windows / Linux / macOS, только stdlib, полный откат.

Topics: `hermes` `hermes-agent` `bugfix` `patcher` `cross-platform` `windows` `linux` `macos`

---

## Release title

```
v1.0.1 — delivery fixes (shim discovery, verification, live-install source patch)
```

Tag: `v1.0.1` (target: `main`)

---

## Release description

```markdown
# Hermes Image Persist 1.0.1

Field fix: 1.0.0 could install yet not act on 0.23.2. Fixes [NousResearch/hermes-agent#142500](https://github.com/NousResearch/hermes-agent/issues/142500)
on Windows, Linux and macOS — and keeps fixing it after Hermes updates.

## The bug

In Text/Auto image-input modes the Hermes gateway persists the
vision-enrichment prose instead of your caption + `@image:` refs, so pasted
screenshots disappear from the Desktop history right after the reply and on
every session reload.

## What you get

- **Three independent layers**: a runtime shim that replaces no Hermes file
  (update-immune), an adaptive source patch with anchors verified against the
  real upstream sources, and an OS-scheduler guard that re-applies everything
  after each Hermes update.
- **Safe by construction**: what the model sees is never modified; Native
  mode, model weights, llama-server, config, keys and the SQLite history are
  never touched. Rows already stored with the prose get a display-only
  projection — the database is never rewritten.
- **Verified**: tested against the real upstream sources of v0.23.1 (`a84025a`)
  and v0.23.2 (`ae2a81c`), both shipped as fixtures; 87 offline checks
  (`python selfcheck.py`), CI on Windows/Linux/macOS, Python 3.9–3.13.
- **No dependencies**: pure stdlib, no network access, preview before any
  change, full rollback via `python rollback.py`.

## Install

Windows (PowerShell, from the unzipped folder — preview first, then add `-Apply`):

    powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\install.ps1

Linux / macOS:

    bash install.sh --apply

Full instructions in four languages (Русский / English / 简体中文 / हिन्दी)
in the README.

## Files

- `hermes-image-persist-1.0.1.zip` — the complete package

When upstream merges its own fix for #142500, simply uninstall this package
(`python rollback.py`).
```
