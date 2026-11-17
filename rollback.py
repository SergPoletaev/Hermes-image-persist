#!/usr/bin/env python3
"""Hermes Image Persist - rollback.

Removes, in order: the OS scheduler entry, the runtime shim from every
environment it was planted into, the source patches (restored from the newest
backup), and the helper file. Nothing else is touched; SQLite history, config
and credentials were never modified.

Usage:
    python rollback.py               # interactive (asks for confirmation)
    python rollback.py --yes         # no confirmation prompt
    python rollback.py --backup DIR  # restore from a specific backup dir
    python rollback.py --purge       # also delete the state dir afterwards
"""
from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hip import discovery, patcher, schedulers, shim_install, state as state_mod  # noqa: E402

CONFIRM_PHRASE = "RESTORE IMAGE PERSIST"


def newest_backup(home: Path):
    backups = state_mod.backups_dir(home)
    if not backups.is_dir():
        return None
    candidates = sorted(
        (d for d in backups.iterdir() if d.is_dir()),
        key=lambda d: d.name, reverse=True)
    return candidates[0] if candidates else None


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Image Persist rollback")
    parser.add_argument("--yes", action="store_true")
    parser.add_argument("--backup", default="", help="specific backup directory")
    parser.add_argument("--root", default="")
    parser.add_argument("--home", default="")
    parser.add_argument("--purge", action="store_true", help="also remove the state dir")
    args = parser.parse_args()

    state = state_mod.load_state(discovery.hermes_home(args.home))
    home = discovery.hermes_home(args.home or state.get("home", ""))
    root = discovery.agent_root(home, args.root or state.get("root", ""))

    print("=== Hermes Image Persist rollback ===")
    print("hermes home : %s" % home)
    print("agent root  : %s" % root)

    backup = Path(args.backup) if args.backup else newest_backup(home)
    if backup is not None and args.backup == "":
        print("newest backup: %s" % backup)
    if args.backup and not Path(args.backup).is_dir():
        print("backup dir not found: %s" % args.backup)
        return 1

    if not args.yes:
        try:
            answer = input('Type "%s" to roll back: ' % CONFIRM_PHRASE)
        except EOFError:
            answer = ""
        if answer.strip() != CONFIRM_PHRASE:
            print("Cancelled, nothing changed.")
            return 0

    # 1) scheduler
    ok, detail = schedulers.unregister()
    print("  scheduler : %s" % detail)

    # 2) shim
    removed = shim_install.remove_shim_everywhere(home, root, recorded=state.get("shim_venvs"))
    print("  shim      : removed from %d environment(s)%s" % (
        len(removed), "" if removed else " (nothing planted)"))

    # 3) source patches
    if root.is_dir():
        restored = []
        if backup is not None and (backup / "run_turn_prepare.py").is_file():
            restored = patcher.restore_backup(root, backup)
            print("  source    : restored from %s (%s)" % (backup.name, ", ".join(restored) or "nothing"))
        else:
            print("  source    : no backup found; markers stay (inert without the shim)")
        if patcher.remove_helper(root):
            print("  helper    : removed gateway/local_image_persist.py")
    else:
        print("  source    : agent root missing, skipped")

    # 4) state
    if args.purge:
        shutil.rmtree(state_mod.state_dir(home), ignore_errors=True)
        print("  state dir : removed")
    else:
        state["last_status"] = "rolled_back"
        state["rolled_back_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        state_mod.save_state(home, state)

    print()
    print("Rollback complete. Restart Hermes (tray included) and re-test Native mode to")
    print("confirm nothing regressed. Old message rows are never modified by either direction.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
