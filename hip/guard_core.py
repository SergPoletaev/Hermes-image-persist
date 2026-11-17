"""Guard core - the "fixation" mechanism that survives Hermes updates.

One pass of the guard does:

1. Locate the Hermes install (state.json pins it; auto-redetect if moved).
2. Re-plant the runtime shim into every live Python environment (always safe,
   even while Hermes runs - it is read at next process start). This covers
   the update paths that break layer 2: a replaced source tree, a new PM
   dependency generation, a rebuilt venv.
3. If Hermes processes are stopped and a source patch is still needed
   (plan_all() says "patched" or the helper is missing), re-apply the source
   patches with a fresh backup.
4. Record everything in state.json + guard.log and compute a status:
   ``ok`` / ``ok_no_source`` (processes were running) / ``needs_adaptation``
   (anchors unrecognizable - send diagnose output for a new anchor).

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

    # 2) source patches - act purely on what plan_all() reports
    plans = patcher.plan_all(root)
    needs_patch = any(p.status in ("patched", "refused") for p in plans.values())
    helper_needed = not patcher.helper_present(root)

    if needs_patch or helper_needed:
        running, detail = discovery.hermes_process_running(root)
        if running or not allow_source:
            report["source"] = {rel: plan.as_dict() for rel, plan in plans.items()}
            if needs_patch:
                report["status"] = "ok_no_source"
                report["notes"].append(
                    "source patch deferred (Hermes running: %s); the runtime shim carries the fix"
                    % detail if running else "source patch disabled by flag")
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

    # 3) state update
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
    state_mod.save_state(home, state)
    for note in report["notes"]:
        state_mod.append_log(home, "[%s] %s" % (report["status"], note))
    state_mod.append_log(
        home, "pass complete: status=%s version=%s shim_venvs=%d"
        % (report["status"], report["version"], len(report.get("shim") or [])))
    return report
