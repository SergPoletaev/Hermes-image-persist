# Changelog

## 1.0.3 (2026-11-18)

Second field report on Hermes v0.23.2 (full logs + diagnostics). The 1.0.2
install had succeeded on paper - patches present, shim planted+verified in the
committed venv, guard "ok" - yet image turns STILL produced zero activation
markers. The logs proved why, twice over:

1. **The gateway survived everything.** No "Starting Hermes Gateway" after
   22:53; the 01:43 image turn was served by a thread of the very same
   process (continuous gateway thread numbering 41 -> 53). Exiting the
   Desktop via the tray did NOT stop the backend, and the llama-server the
   user killed by hand was respawned at 01:43:05 BY THE OLD GATEWAY (proof
   it was alive before the Desktop restart).
2. **v1.0.2's --restart-gateway never worked.** It asked PowerShell to match
   'gateway.run'/'serve' in command lines - the probe's OWN command line
   matched (visible in the user's diagnostics: "gateway: powershell.exe ...
   Get-CimInstance ..."), so the installer kept finding and killing
   ephemeral copies of itself and hung after "Stopping the running Hermes
   gateway...". The real gateway was never touched.

What 1.0.3 does:

- **Process-table classification (no more PowerShell matching).** One CIM
  query fetches every process (pid, ppid, name, cmdline, start time); the
  gateway is classified IN PYTHON: command-line tokens (-m gateway.run /
  serve / tui_gateway), Desktop & Chromium children excluded, probe shells
  (powershell/pwsh/cmd/wmic carrying our own tokens) marked pid=-1 and never
  selectable - the self-match bug is structurally impossible now.
- **llama-server parent chain.** An opaque gateway (hermes.exe with no
  readable args) is still detected THROUGH its supervised llama-server
  child - the exact field evidence (llama respawned by the old gateway).
- **The installer now ALWAYS stops a running gateway** (default on;
  --no-restart-gateway to opt out). v1.0.2 required a flag nobody knew
  about; v1.0.3 makes "install -> start Hermes -> fixed" the default path.
  Each kill is taskkill /T /F once, then one fresh-snapshot verification.
- **Standalone-Python verification fixed.** tools/python-* environments keep
  python.exe in the ROOT, not Scripts/; venv_python() now resolves them, so
  the permanent "no python in this environment / UNVERIFIED" noise is gone.
- diagnose.py prints each gateway's pid, command line, start time, the
  detection route, and a loud STARTED BEFORE THE FIX marker; the guard note
  lists the stale pids.
- Tests: 122 offline checks (was 111), incl. probe self-exclusion, Desktop
  exclusion, llama-chain detection, CIM /Date() parsing, standalone-python
  resolution; plus a live field simulation (fake gateway + supervised
  llama-server: detected, flagged, stopped, guard clean).

## 1.0.2 (2026-11-18)

Field fix for the 1.0.1 report on Hermes v0.23.2 with full logs. The logs
proved the fix was installed PERFECTLY — source patches byte-identical,
shim planted and verified in the committed venv, 14 shim-active processes —
yet not a single activation marker fired. Root cause:

- **The gateway outlives the Desktop.** The Hermes backend runs as a
  long-lived background `serve` process. Closing and reopening the Desktop
  (what the 1.0.1 instructions asked for) does NOT restart it. The user's
  gateway had started at 22:53, the fix was planted at 00:58, and the very
  next image turn (00:59) was still served by the pre-fix process with old
  code in memory. Python loads code at process start; neither the .pth nor
  the patched sources can reach an already-running interpreter.

What 1.0.2 adds:

- **Gateway-liveness audit.** `live_gateway_commands()` identifies the actual
  gateway processes (`-m gateway.run` / `serve`) among all Hermes processes
  (Desktop Chromium children excluded); `process_start_epochs()` resolves
  their start times (Win32_Process CreationDate / /proc). The guard stores
  `first_installed_epoch` and every pass compares: a gateway started before
  the fix flips the status to **`needs_gateway_restart`** with an explicit
  instruction.
- **`--restart-gateway` installer flag.** Stops the running gateway
  (graceful taskkill/SIGTERM first so SessionDB teardown handlers run, hard
  kill only for survivors; llama-server is supervised and re-spawns on
  demand) so the next Desktop start loads the fix. The installer now always
  reports when a live gateway is still running old code.
- **diagnose.py** gained a "GATEWAY PROCESS" section showing each gateway's
  command line, start time, and a loud `<-- STARTED BEFORE THE FIX` marker
  plus the restart instruction; the verdict checklist explains the new
  `needs_gateway_restart` status.
- Tests: 111 offline checks (was 106) — gateway classification, start-epoch
  resolution, pre-fix gateway flips the guard status, post-fix gateway does
  not.

## 1.0.1 (2026-11-17)

Field fix for the 1.0.0 regression reported on Hermes v0.23.2: the fix was
installed but pasted images STILL vanished after the reply. Root causes
addressed (all three are delivery failures — the payload logic itself was
verified correct against the real 0.23.2 sources):

- **Environment discovery hardened (layer 1).** `committed_environment()` now
  reads `facts.json` under EVERY `installs/*/` state dir, not only the
  crc32-keyed one for the current agent root — a PM re-key between Hermes
  versions previously made the committed venv invisible, so the shim was
  planted into environments the gateway never runs on. `discover_venvs()` also
  scans sibling state dirs for their `environments/` generations, and — the
  most reliable signal — adds the interpreters of LIVE Hermes processes
  (Win32_Process / ps / /proc command lines) as `liveprocess` entries.
- **Shim verification (layer 1).** After planting, the shim is proven to
  import AND install its import hook from the target environment's own
  interpreter (`python -I`, the same isolation the Hermes launchers use).
  A `.pth` file can sit on disk while never executing — 1.0.0 reported
  "planted" in exactly that situation. Installers, the guard, and
  `diagnose.py` now all surface `VERIFIED` / `PLANTED (UNVERIFIED)` per
  environment.
- **Source patch no longer deferred while Hermes runs (layer 2).** 1.0.0
  postponed the source patch whenever Hermes processes were detected — with an
  always-running Desktop that meant "forever", leaving only the (mis-planted)
  shim. The patch is now applied immediately: the write is atomic
  (tmp + `os.replace`) and compile-checked; a running interpreter keeps its
  imported module in memory and the next process start picks the patch up.
  A note tells the user to restart Hermes.
- **Third write seam wrapped by the shim (layer 1).** `gateway.run_turn`'s
  module-level binding of `strip_discord_triggering_note` (queued follow-up
  persist path — the seam the source patch anchors to in `run_turn.py`) is now
  wrapped too, covering builds where the import was hoisted.
- **Path handling.** `file://` URLs in enrichment notes are unwrapped to real
  paths (including `file:///C:/...` and `file:////tmp/x` shapes); bare file
  names inside the note resolve against the event's attachment directories
  (inbound pre-analysis can swap paths for cached copies). POSIX refs never
  keep a `//` prefix.
- **Diagnostics.** `diagnose.py` shows live Hermes command lines, verifies the
  shim in every environment, and gained `--out <file>` to write the full
  report for sharing. The verdict checklist explains every failure pattern.
- **Tests.** 111 offline checks (was 90/87): shim verification in a real venv
  (`.pth` executes under `-I`), the `run_turn` hoisted seam, file:// and
  bare-name path recovery, sibling `facts.json` discovery, live command-line
  → env-root mapping, and a regression guard proving the source patch is
  applied even with a live Hermes process.

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
- **Offline verification suite**: 111 checks, including the real upstream
  sources of both versions as fixtures, byte-identical replacement tests for
  upgrades from earlier builds, and an integration test that imports the real
  0.23.2 `run_turn_prepare.py` through the shim.
