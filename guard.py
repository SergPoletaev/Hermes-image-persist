#!/usr/bin/env python3
"""Hermes Image Persist - guard (one maintenance pass).

Invoked hourly / at login by the OS scheduler registered by install.py, and
safe to run manually at any time. Re-plants the runtime shim after Hermes
updates (new dependency generations, rebuilt venvs) and re-applies the source
patch once Hermes is stopped. Writes state.json + guard.log; never raises.

Usage:
    python guard.py            # one pass, human-readable output
    python guard.py --quiet    # one pass, only warnings (scheduler mode)
    python guard.py --status   # print the last recorded status, no changes
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hip import discovery, guard_core, schedulers, state as state_mod  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Image Persist guard")
    parser.add_argument("--quiet", action="store_true", help="minimal output (scheduler mode)")
    parser.add_argument("--status", action="store_true", help="print last status and exit")
    parser.add_argument("--no-source", action="store_true", help="never touch source files this pass")
    args = parser.parse_args()

    home = discovery.hermes_home()
    state = state_mod.load_state(home)

    if args.status:
        print("status        : %s" % state.get("last_status", "never run"))
        print("last pass     : %s" % state.get("last_guard", "-"))
        print("agent root    : %s" % state.get("root", "-"))
        print("version seen  : %s" % (state.get("last_report") or {}).get("version", "-"))
        print("scheduler     : %s" % schedulers.registered())
        report = state.get("last_report") or {}
        for note in report.get("notes", []):
            print("  note: %s" % note)
        return 0

    report = guard_core.guard_pass(home, state, allow_source=not args.no_source)

    if args.quiet:
        if report["status"] == "needs_adaptation":
            print("WARNING: source layout unrecognized; shim active. Run diagnose.py for details.")
        return 0

    print("=== Hermes Image Persist guard pass ===")
    print("status          : %s" % report["status"])
    print("hermes version  : %s" % report["version"])
    print("shim environments:")
    for entry in report.get("shim") or []:
        print("  [%s] %s -> %s" % (entry["kind"], entry["venv"], "ok" if entry["ok"] else entry["detail"]))
    for note in report.get("notes", []):
        print("  note: %s" % note)
    return 0


if __name__ == "__main__":
    sys.exit(main())
