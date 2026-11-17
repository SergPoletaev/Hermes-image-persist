# Changelog

## 1.0.0 (2026-11-17)

First public release. Cross-platform fix for
[NousResearch/hermes-agent#142500](https://github.com/NousResearch/hermes-agent/issues/142500)
— pasted images vanish from the Desktop chat history in Text/Auto modes — with
an update-survival mechanism and universal installers for Windows, Linux and
macOS.

- **Runtime shim (layer 1)**: `.pth` + module planted into the Python
  environment the gateway actually runs on (PM committed generation, sibling
  generations, legacy in-tree venvs). Activated by Hermes' own
  `site.addsitedir()` bootstrap path in every gateway/web/TUI process,
  including `python -I` launchers. Wraps:
  - `GatewayTurnPrepareMixin._hmwa_apply_message_timestamp` (main turns),
  - `gateway.run_inbound.strip_discord_triggering_note` (queued follow-ups),
  - `hermes_cli.web_routers.sessions._project_for_display` (display-only
    projection for legacy rows; the DB is never rewritten).
- **Adaptive source patch (layer 2)**: exact anchors verified byte-for-byte
  against the real 0.23.1 and 0.23.2 sources, with whitespace-tolerant and
  structural fallbacks; every write is compile-checked, atomic and backed up;
  unknown layouts fail closed; blocks left by earlier private builds of this
  fix are replaced automatically on upgrade.
- **Guard (layer 3)**: OS scheduler entry (Task Scheduler / LaunchAgent /
  systemd user timer / cron) re-plants the shim after dependency-generation
  switches and re-applies source patches after tree updates; status
  `needs_adaptation` when anchors change beyond recognition.
- **Durable text matches the upstream canonical form**
  (`caption\n@image:...`, single newline) and the write path is
  platform-agnostic, mirroring the community fix for #142500.
- **Path handling**: paths with spaces are supported in note refs
  (quoted like upstream `format_reference_value`).
- **Cross-platform installers**: `install.py` (pure stdlib, 3.9+) plus
  `install.ps1` / `install.sh` wrappers; universal `diagnose.py` and
  `rollback.py`.
- **Offline verification suite**: 87 checks, including the real upstream
  sources of both versions as fixtures, byte-identical replacement tests for
  upgrades from earlier builds, and an integration test that imports the real
  0.23.2 `run_turn_prepare.py` through the shim.
