"""Guard core - the "fixation" mechanism that survives Hermes updates.

One pass of the guard does:

1. Locate the Hermes install (state.json pins it; auto-redetect if moved).
2. Re-plant the runtime shim into every live Python environment (always safe,
   even while Hermes runs - it is read at next process start) and VERIFY it
   imports + installs its hook from each environment's own interpreter. This
   covers the update paths that break layer 2: a replaced source tree, a new
   PM dependency generation, a rebuilt venv.
3. If a source patch is still needed (plan_all() says "patched" or the helper
   is missing), apply it - as of v1.0.1 immediately, even while Hermes runs:
   the write is atomic and compile-checked, a running interpreter keeps its
   imported module in memory, and the next process start picks up the patch.
4. Record everything in state.json + guard.log and compute a status:
   ``ok`` / ``needs_adaptation`` (anchors unrecognizable - send diagnose
   output for a new anchor).

The previous sha256/fingerprint change-detection step was removed: it was
redundant with plan_all() (which already tells us whether a patch is needed)
and it flipped to "changed" on any tiny edit to a file under the agent root,
which made the guard noisy and brittle. The guard now acts purely on what
plan_all() reports - no content hashing, no false positives.
"""
from __future__ import annotations

import time
from pathlib import Path

from . import discovery, patcher, schedulers, shim_install, state as state_mod


def ensure_layers(home: Path, root: Path, state: dict, *, allow_source: bool = True,
                  quiet: bool = False) -> dict:
    """One guard pass. Mutates and returns an updated report dict."""
    report = {
        "when": time.strftime("%Y-%m-%d %H:%M:%S"),
        "home": str(home),
        "root": str(root),
        "version": discovery.version_string(root),
        "shim": [],
        "source": {},
        "status": "ok",
        "notes": [],
    }

    # 1) shim into every environment
    venvs = discovery.discover_venvs(home, root)
    report["shim"] = shim_install.install_shim_everywhere(home, root, venvs=venvs)
    if not venvs:
        report["notes"].append("no Python environment discovered; shim not planted anywhere")
    unverified = [e for e in report["shim"] if e.get("unverified")]
    if unverified:
        report["notes"].append(
            "shim UNVERIFIED in %d environment(s): %s" % (
                len(unverified), "; ".join(e["detail"][-120:] for e in unverified[:2])))

    # 2) source patches - act purely on what plan_all() reports.
    # v1.0.1: applied IMMEDIATELY, including while Hermes is running. The write
    # is atomic (tmp + os.replace) and compile-checked; a running interpreter
    # keeps its already-imported module in memory, and the next process start
    # picks up the patched file. 1.0.0 deferred this step while Hermes was
    # running, and with an always-running Desktop the patch was postponed
    # forever - one of the two field failure modes of that release.
    plans = patcher.plan_all(root)
    needs_patch = any(p.status in ("patched", "refused") for p in plans.values())
    helper_needed = not patcher.helper_present(root)

    if needs_patch or helper_needed:
        if not allow_source:
            report["source"] = {rel: plan.as_dict() for rel, plan in plans.items()}
            report["notes"].append("source patch disabled by flag")
        else:
            backup_dir = state_mod.backups_dir(home) / time.strftime("%Y%m%d-%H%M%S")
            applied = patcher.apply_plans(root, plans, backup_dir)
            patcher.install_helper(root)
            report["source"] = {rel: plan.as_dict() for rel, plan in plans.items()}
            report["backup"] = str(backup_dir)
            if any(p.status == "refused" for p in plans.values()):
                report["status"] = "needs_adaptation"
                report["notes"].append(
                    "source layout not recognized; run diagnose.py and report it. "
                    "The runtime shim still fixes new turns.")
            if applied:
                report["notes"].append("source patches applied: %s" % ", ".join(sorted(applied)))
    else:
        report["source"] = {rel: plan.as_dict() for rel, plan in plans.items()}

    # 3) gateway-liveness audit (v1.0.2, hardened in v1.0.3).
    # The gateway is a long-lived background 'serve' process that SURVIVES
    # Desktop restarts AND tray exits. A gateway that started BEFORE the fix
    # was planted still runs pre-patch code from memory - the exact field
    # failure of 1.0.1/1.0.2 (gateway from 22:53, fix at 00:58, then again at
    # 01:42, zero activation markers both times). v1.0.3 uses the process-TABLE
    # classifier (probe-shell self-matching fixed) incl. the llama-server
    # parent chain, and flags any gateway older than the fix.
    gateway_procs = discovery.live_gateway_processes()
    if gateway_procs:
        fix_epoch = state.get("first_installed_epoch")
        stale = [g for g in gateway_procs
                 if g.get("start_epoch") and fix_epoch and g["start_epoch"] < fix_epoch]
        report["gateway_processes"] = [
            {"pid": g.get("pid"), "name": g.get("name"),
             "cmdline": (g.get("cmdline") or "")[:200],
             "started_epoch": g.get("start_epoch"),
             "via": g.get("via")}
            for g in gateway_procs[:6]]
        if stale:
            report["status"] = "needs_gateway_restart"
            report["notes"].append(
                "%d gateway process(es) started BEFORE the fix (pids %s); they still run "
                "pre-fix code in memory. %s"
                % (len(stale), ", ".join(str(g.get("pid")) for g in stale[:4]),
                   discovery.gateway_restart_hint()))
        else:
            report["notes"].append(
                "gateway process(es) running: %d (started after the fix - ok)"
                % len(gateway_procs))
    else:
        report["gateway_processes"] = []

    # 4) state update
    state["last_guard"] = report["when"]
    state["last_status"] = report["status"]
    state["last_report"] = report
    state.setdefault("history", []).append(
        {"when": report["when"], "status": report["status"], "version": report["version"]})
    state["history"] = state["history"][-40:]
    state["shim_venvs"] = sorted({entry["venv"] for entry in report["shim"] if entry["ok"]})
    return report


def guard_pass(home: Path, state: dict, *, allow_source: bool = True, quiet: bool = False) -> dict:
    """ensure_layers + persistence of state + log lines."""
    root = Path(state.get("root") or discovery.agent_root(home))
    if not root.is_dir():
        root = discovery.agent_root(home)
    report = ensure_layers(home, root, state, allow_source=allow_source, quiet=quiet)
    state["root"] = str(root)
    state.setdefault("first_installed_epoch", state.get("first_installed_epoch") or time.time())
    state_mod.save_state(home, state)
    for note in report["notes"]:
        state_mod.append_log(home, "[%s] %s" % (report["status"], note))
    state_mod.append_log(
        home, "pass complete: status=%s version=%s shim_venvs=%d"
        % (report["status"], report["version"], len(report.get("shim") or [])))
    return report
