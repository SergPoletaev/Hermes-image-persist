#!/usr/bin/env python3
"""Hermes Image Persist - universal installer (Windows / Linux / macOS).

Fixes upstream NousResearch/hermes-agent#142500: in Text/Auto modes the
vision-enrichment prose was persisted instead of the user's caption +
``@image:`` refs, so pasted screenshots disappeared from the Desktop history
after the reply and on session reload.

Three layers are installed:

1. Runtime shim (primary, immune to Hermes updates): a .pth + module planted
   into the site-packages of the Python environment the gateway runs on.
   Hermes' own bootstrap activates that environment via site.addsitedir(),
   which executes .pth files in every gateway/web/TUI process.
2. Source patch (secondary): durable-text swap inserted into
   gateway/run_turn_prepare.py (main turns) and gateway/run_turn.py (queued
   follow-ups), plus the inert helper gateway/local_image_persist.py.
   Replaced automatically on Hermes updates by the guard.
3. Guard (fixation): an OS scheduler entry (Task Scheduler / LaunchAgent /
   systemd user timer / cron) that re-plants the shim and re-applies the
   source patch after every Hermes update.

Usage:
    python install.py            # preview only, no changes
    python install.py --apply    # install (asks for confirmation)
    python install.py --apply --yes            # no confirmation prompt
    python install.py --apply --no-source      # shim + guard only
    python install.py --apply --no-guard       # no scheduler entry
    python install.py --root <path> --home <path>   # explicit layout

Pure stdlib. Python 3.9+. No network access, no dependencies installed.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hip import discovery, guard_core, patcher, schedulers, shim_install, state as state_mod  # noqa: E402

CONFIRM_PHRASE = "PATCH IMAGE PERSIST"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true", help="apply changes (default: preview only)")
    parser.add_argument("--yes", action="store_true", help="skip the confirmation prompt")
    parser.add_argument("--root", default="", help="hermes-agent directory (default: <home>/hermes-agent)")
    parser.add_argument("--home", default="", help="Hermes home (default: HERMES_HOME or platform default)")
    parser.add_argument("--no-source", action="store_true", help="skip the source patch layer")
    parser.add_argument("--no-shim", action="store_true", help="skip the runtime shim layer")
    parser.add_argument("--no-guard", action="store_true", help="skip the OS scheduler registration")
    parser.add_argument("--no-restart-gateway", action="store_true",
                        help="do NOT stop a running Hermes gateway (default: stop it - "
                             "it survives Desktop restarts and holds pre-fix code in memory)")
    args = parser.parse_args()

    home = discovery.hermes_home(args.home)
    root = discovery.agent_root(home, args.root)
    repo_dir = Path(__file__).resolve().parent

    print("=== Hermes Image Persist 1.0.3 ===")
    print("hermes home : %s" % home)
    print("agent root  : %s" % root)
    if not root.is_dir():
        print()
        print("AGENT ROOT NOT FOUND." if not args.root else "AGENT ROOT NOT FOUND: %s" % root)
        print("Pass --root <path-to-hermes-agent> (and --home if non-default).")
        return 1

    version = discovery.version_string(root)
    print("hermes ver  : %s" % version)

    # -- layer 2 plan -------------------------------------------------------
    plans = patcher.plan_all(root)
    print()
    print("--- layer 2: source patch ---")
    for rel, plan in plans.items():
        detail = {"patched": "ready (%s anchor)" % plan.matched_anchor,
                  "already": "already installed",
                  "refused": "REFUSED: %s" % plan.reason,
                  "missing": "file missing (%s)" % plan.reason,
                  "skipped": "skipped: %s" % plan.reason,
                  "unpatched": "not planned"}[plan.status]
        print("  %-32s %s" % (Path(rel).name, detail))
    helper = patcher.helper_present(root)
    print("  %-32s %s" % ("local_image_persist.py", "present" if helper else "will be created"))

    # -- layer 1 plan -------------------------------------------------------
    venvs = discovery.discover_venvs(home, root)
    print()
    print("--- layer 1: runtime shim (update-immune) ---")
    if not venvs:
        print("  no Python environment discovered - the source patch will be the only layer!")
    for venv, kind in venvs:
        print("  [%s] %s  (%s)" % (kind, venv, "shim present" if shim_install.shim_installed(venv) else "will plant"))
    if not venvs:
        print("  hint: run the installer with the Hermes venv's python if detection misses it")

    # -- layer 3 plan -------------------------------------------------------
    print()
    print("--- layer 3: guard (survives updates) ---")
    print("  scheduler  : %s" % schedulers.registered())

    refused = [p for p in plans.values() if p.status == "refused"]
    if refused:
        print()
        print("WARNING: source layout was NOT recognized (%s)." % refused[0].reason)
        print("The runtime shim (layer 1) still fixes new turns on its own; for the source")
        print("patch, run diagnose.py and send its output for an adapted anchor.")
        if not venvs:
            print()
            print("NO LAYER COULD BE PLANNED. Nothing will be changed.")
            return 1

    print()
    print("NOT touched: Native mode, model weights, llama-server, config.yaml, keys,")
    print("SQLite history (old rows are never rewritten), Desktop binaries.")
    print("Old rows stored with enrichment prose get a display-only projection via the shim.")

    if not args.apply:
        print()
        print("PREVIEW ONLY - no modifications made. Re-run with --apply to install.")
        return 0

    if not args.yes:
        try:
            answer = input('Type "%s" to apply: ' % CONFIRM_PHRASE)
        except EOFError:
            answer = ""
        if answer.strip() != CONFIRM_PHRASE:
            print("Cancelled, nothing changed.")
            return 0

    state = state_mod.load_state(home)
    state.setdefault("installed_at", time.strftime("%Y-%m-%d %H:%M:%S"))
    state["root"] = str(root)
    state["home"] = str(home)
    state["repo_dir"] = str(repo_dir)
    state["options"] = {
        "source": not args.no_source, "shim": not args.no_shim, "guard": not args.no_guard,
    }

    applied_notes = []
    hermes_running = False

    # layer 1: shim
    if not args.no_shim:
        results = shim_install.install_shim_everywhere(home, root, venvs=venvs)
        ok = [r for r in results if r["ok"]]
        bad = [r for r in results if not r["ok"]]
        unverified = [r for r in results if r.get("unverified")]
        applied_notes.append("runtime shim planted into %d environment(s)" % len(ok))
        for entry in bad:
            applied_notes.append("shim FAILED for %s (%s)" % (entry["venv"], entry["detail"]))
        for entry in unverified:
            applied_notes.append("shim UNVERIFIED for %s (%s)" % (entry["venv"], entry["detail"]))

    # layer 2: source patch - v1.0.1 applies it immediately. The write is
    # atomic + compile-checked; a RUNNING Hermes keeps its imported modules in
    # memory and picks the patch up at the next process start. (1.0.0 deferred
    # this while Hermes was running, which with an always-running Desktop meant
    # the patch never landed at all.)
    if not args.no_source:
        running, detail = discovery.hermes_process_running(root)
        hermes_running = bool(running)
        backup_dir = state_mod.backups_dir(home) / time.strftime("%Y%m%d-%H%M%S")
        applied = patcher.apply_plans(root, plans, backup_dir)
        patcher.install_helper(root)
        state["source_patch_pending"] = False
        state["backup"] = str(backup_dir)
        applied_notes.append("source patches: %s" % (", ".join(sorted(applied)) or "none needed"))
        if hermes_running:
            applied_notes.append(
                "Hermes is running (%s): RESTART it (tray included) so new processes"
                % detail[:80] + " pick up the shim + patch")

    # layer 3: guard
    if not args.no_guard:
        ok, detail = schedulers.register(repo_dir)
        applied_notes.append("guard registration: %s" % detail)

    # v1.0.2: remember WHEN the fix landed - the guard uses this to flag
    # gateway processes that predate the fix (they run pre-fix code in memory).
    state.setdefault("first_installed_epoch", time.time())

    # one guard pass right away (fills gaps, records state)
    report = guard_core.guard_pass(home, state, allow_source=not args.no_source)
    applied_notes.append("status: %s" % report["status"])

    # v1.0.3: the gateway is a long-lived background 'serve' process that
    # SURVIVES Desktop restarts AND tray exits (field-proven: a gateway from
    # 22:53 kept serving image turns after two Desktop restarts). A running
    # gateway still has OLD code in memory, so the fix CANNOT act until it is
    # restarted. The Desktop offers no way to stop it - so we stop it HERE,
    # ALWAYS, unless --no-restart-gateway was given. Stopping the gateway also
    # closes the Desktop backend connection; simply start Hermes again after.
    gateway_procs = discovery.live_gateway_processes()
    if gateway_procs:
        print()
        print("Found %d running Hermes GATEWAY process(es) (they survive Desktop"
              % len(gateway_procs) + " restarts and hold OLD pre-fix code in memory).")
        for proc in gateway_procs[:4]:
            started = proc.get("start_epoch")
            when = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(started)) if started else "?"
            print("  - %s (pid %d), started %s" % ((proc.get("name") or "?"), proc["pid"], when))
        if args.no_restart_gateway:
            applied_notes.append(
                "gateway LEFT RUNNING (--no-restart-gateway): the fix will NOT act"
                " until it is restarted - fully exit Hermes and start it again")
        else:
            print()
            print("Stopping the gateway now so the next Hermes start loads the fix...")
            stopped, sdetail = stop_gateway_processes()
            applied_notes.append("gateway restart: %s" % sdetail)
            if stopped:
                applied_notes.append(
                    "ACTION: start Hermes (Desktop) again NOW - the new gateway"
                    " will run with the fix. Your sessions and history are safe.")
            else:
                applied_notes.append(
                    "ACTION (manual): " + sdetail[sdetail.rfind("("):] if "(" in sdetail else sdetail)
    else:
        applied_notes.append(
            "no gateway process running - the next Hermes start will load the fix")

    print()
    for note in applied_notes:
        print("  - %s" % note)
    print()
    print("Installed. VERIFY (fresh session, Text mode, paste a PNG, wait for the reply):")
    print("  1. The thumbnail stays after the reply; only your caption + image remain.")
    print("  2. Switch to another session and back - the thumbnail must survive.")
    print("  3. Repeat in Auto mode; Native mode must behave exactly as before.")
    print("  4. Diagnostics at any time:  python diagnose.py")
    print("  5. Log marker:               Image persistence: durable image references set")
    return 0


def stop_gateway_processes(*, timeout_each: float = 15.0) -> tuple:
    """v1.0.3: stop the REAL gateway processes found by the table classifier.

    v1.0.2 asked PowerShell to match 'gateway.run'/'serve' in command lines -
    its own probe matched, installers chased ephemeral powershell children and
    the real gateway (hermes.exe serve / python -m gateway.run) was never
    touched, so the command appeared to hang after 'Stopping...'. Now one
    process-table snapshot feeds the same classifier as diagnose.py, probe
    shells are excluded by pid=-1, and each target is killed ONCE with
    taskkill /T /F (POSIX: SIGTERM, then SIGKILL). llama-server is supervised
    by the gateway and re-spawns on demand; the Desktop reconnects on relaunch.
    Returns (all_stopped, detail).
    """
    import signal
    import subprocess

    procs = discovery.live_gateway_processes()
    if not procs:
        return True, "no gateway processes found (already stopped)"

    mine = os.getpid()
    steps = []
    for proc in procs:
        pid = proc["pid"]
        if pid <= 0 or pid == mine:
            continue
        label = "%s (pid %d%s)" % (
            (proc.get("name") or "?"), pid,
            ", via llama-server chain" if proc.get("via") else "")
        try:
            if os.name == "nt":
                out = subprocess.run(
                    ["taskkill.exe", "/PID", str(pid), "/T", "/F"],
                    capture_output=True, text=True, timeout=timeout_each)
                steps.append("%s: taskkill rc=%d" % (label, out.returncode))
            else:
                os.kill(pid, signal.SIGTERM)
                steps.append("%s: SIGTERM" % label)
        except Exception as exc:
            steps.append("%s: %r" % (label, exc))

    # let them die, then verify with a FRESH snapshot
    import time as _time

    deadline = _time.time() + 30
    while _time.time() < deadline:
        left = [g for g in discovery.live_gateway_processes()
                if g["pid"] > 0 and g["pid"] != mine]
        if not left:
            return True, "; ".join(steps) + " -> gateway stopped"
        # POSIX second chance: SIGKILL survivors
        if os.name != "nt":
            for g in left:
                try:
                    os.kill(g["pid"], signal.SIGKILL)
                    steps.append("pid %d: SIGKILL" % g["pid"])
                except Exception:
                    pass
        _time.sleep(1.0)
    left = [g for g in discovery.live_gateway_processes() if g["pid"] > 0 and g["pid"] != mine]
    if not left:
        return True, "; ".join(steps) + " -> gateway stopped"
    return False, ("; ".join(steps)
                   + " -> STILL RUNNING (pids %s): open Task Manager, end 'Hermes' "
                     "(all of them) and any 'hermes' python processes, then start "
                     "Hermes again" % ", ".join(str(g["pid"]) for g in left[:5]))


if __name__ == "__main__":
    sys.exit(main())
