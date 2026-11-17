#!/usr/bin/env python3
"""Hermes Image Persist - read-only diagnostics.

Everything one screen: install discovery, fix layers (current source markers,
legacy markers left by earlier private builds, runtime shim per environment,
guard registration and last pass), the newest user rows in state.db, the
relevant log markers, and - when Hermes is running - the REST projection the
Desktop actually renders.

Never modifies anything (SQLite is opened read-only; REST calls are GETs).

Usage:
    python diagnose.py [--root PATH] [--home PATH] [--session ID] [--port N]

(This script is named diagnose.py - NOT inspect.py - so it does not shadow
the Python standard-library ``inspect`` module that ``py_compile`` /
``traceback`` rely on. On Python 3.14+ the stdlib ``_colorize`` module pulls
in ``inspect.signature`` at import time, so a top-level inspect.py in the
project directory breaks every other script in the package.)
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from hip import discovery, patcher, schedulers, shim_install, state as state_mod  # noqa: E402

NOTE_PREFIXES = (
    "[The user sent an image", "[The user attached an image", "[The user shared an image",
    "[The user provided an image", "[The user uploaded an image",
)


def hr(title: str) -> None:
    print()
    print("=" * 78)
    print(title)
    print("=" * 78)


def section_layers(home: Path, root: Path) -> None:
    hr("1) FIX LAYERS")
    print("hermes home : %s" % home)
    print("agent root  : %s" % root)
    print("hermes ver  : %s" % discovery.version_string(root))

    print()
    print("-- source markers (layer 2) --")
    markers = patcher.markers_in(root)
    for label, info in markers.items():
        print("  [%s] %-34s %s" % ("PRESENT" if info["present"] else "absent ", label, info["file"]))

    print()
    print("-- runtime shim (layer 1) --")
    venvs = discovery.discover_venvs(home, root)
    committed = discovery.committed_environment(home, root)
    if committed is not None:
        print("  committed environment: %s" % committed)
    if not venvs:
        print("  no Python environments discovered")
    for venv, kind in venvs:
        module, pth = shim_install.shim_paths_for_venv(venv)
        print("  [%s] %-9s module=%s pth=%s" % (
            "OK " if shim_install.shim_installed(venv) else "off",
            kind, "yes" if module.is_file() else "no ", "yes" if pth.is_file() else "no "))

    print()
    print("-- guard (layer 3) --")
    print("  registration : %s" % schedulers.registered())
    state = state_mod.load_state(home)
    print("  last pass    : %s" % state.get("last_guard", "never"))
    print("  last status  : %s" % state.get("last_status", "-"))
    report = state.get("last_report") or {}
    for note in report.get("notes", []):
        print("  note: %s" % note)
    history = state.get("history") or []
    for entry in history[-6:]:
        print("    %s  %-16s %s" % (entry.get("when", "?"), entry.get("status", "?"), entry.get("version", "")))


def find_state_dbs(base: Path, limit: int = 6) -> list:
    found = []
    skip = {"hermes-agent", "node_modules", "runtimes", "tools", "bin", "logs", "cache", "hermes-image-persist"}
    for dirpath, dirnames, filenames in os.walk(base):
        dirnames[:] = [d for d in dirnames if d not in skip and not d.startswith(".")]
        for name in filenames:
            if name.lower() in ("state.db", "state.sqlite", "sessions.db"):
                full = Path(dirpath) / name
                try:
                    found.append((full.stat().st_mtime, full))
                except OSError:
                    pass
        if len(found) > 400:
            break
    found.sort(reverse=True)
    return [path for _mtime, path in found[:limit]]


def dump_recent_user_rows(db_path: Path, row_limit: int = 12) -> None:
    uri = "file:%s?mode=ro" % str(db_path).replace("\\", "/").replace("?", "%3f").replace("#", "%23")
    try:
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        print("  cannot open read-only: %r" % (exc,))
        return
    try:
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(messages)")
        cols = [row[1] for row in cur.fetchall()]
        if not cols:
            print("  no messages table in this DB")
            return
        print("  columns: %s" % ", ".join(cols))
        content_col = "content" if "content" in cols else ("text" if "text" in cols else None)
        role_col = "role" if "role" in cols else None
        if not content_col:
            print("  no content/text column found")
            return
        select = ["rowid", content_col] + ([role_col] if role_col else [])
        cur.execute("SELECT %s FROM messages ORDER BY rowid DESC LIMIT %d"
                    % (", ".join(select), row_limit * 3))
        shown = 0
        for row in cur.fetchall():
            content = row[1] if isinstance(row[1], str) else ("<non-text>" if row[1] is not None else "")
            role = row[2] if role_col else "-"
            prefix = content.lstrip()[:180]
            flags = [
                "NOTE" if content.lstrip().startswith(NOTE_PREFIXES) else "-",
                "IMG" if "@image:" in content else "-",
                "URL" if "image_url:" in content else "-",
            ]
            print("  row=%s role=%s [%s] %s" % (row[0], role, "/".join(flags),
                                                prefix.replace("\n", "\\n")[:170]))
            shown += 1
            if shown >= row_limit:
                break
    except Exception as exc:
        print("  cannot query messages: %r" % (exc,))
    finally:
        conn.close()


def section_database(home: Path, root: Path) -> None:
    hr("2) STATE.DB (newest rows: IMG = fixed persistence, NOTE = enrichment prose)")
    bases = [home] + ([root] if root != home else [])
    dbs = []
    for base in bases:
        dbs.extend(find_state_dbs(Path(base)))
    seen = set()
    unique = []
    for path in dbs:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    if not unique:
        print("  no state.db found under %s" % home)
        return
    for path in unique[:3]:
        print("  found: %s" % path)
    dump_recent_user_rows(unique[0])


def section_logs(home: Path) -> None:
    hr("3) LOG MARKERS")
    logs_dir = Path(home) / "logs"
    sources = [
        logs_dir / "agent.log",
        logs_dir / "gateway.log",
        Path(home) / "hermes-image-persist" / "shim.log",
        Path(home) / "hermes-image-persist" / "guard.log",
    ]
    pattern = re.compile(
        r"Image persistence|Image display|Image routing:|needs_adaptation")
    for path in sources:
        if not path.is_file():
            continue
        print("--- %s" % path)
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except Exception as exc:
            print("    unreadable: %r" % (exc,))
            continue
        hits = [ln for ln in lines if pattern.search(ln)]
        for ln in hits[-14:]:
            print("    %s" % ln[:220])
        if not hits:
            print("    (no persistence markers yet - run one Text-mode image turn first)")


def discover_port(home: Path) -> int:
    desktop_log = Path(home) / "logs" / "desktop.log"
    try:
        text = desktop_log.read_text(encoding="utf-8", errors="replace")
        matches = re.findall(r"HERMES_BACKEND_READY port=(\d+)", text)
        if matches:
            return int(matches[-1])
    except Exception:
        pass
    return 0


def section_rest(home: Path, session_id: str, port: int) -> None:
    hr("4) REST PROJECTION (what the Desktop renders)")
    if port <= 0:
        port = discover_port(home)
    if port <= 0:
        print("  backend port not discovered (no HERMES_BACKEND_READY in desktop.log).")
        print("  start Hermes and re-run with --port N, or skip this section.")
        return
    base = "http://127.0.0.1:%d" % port
    print("  backend port: %d" % port)
    try:
        if not session_id:
            with urllib.request.urlopen(base + "/api/sessions?limit=5&offset=0&min_messages=0"
                                        "&archived=exclude&order=recent", timeout=10) as resp:
                listing = json.loads(resp.read().decode("utf-8", "replace"))
            sessions = listing.get("sessions") or []
            if sessions:
                session_id = sessions[0].get("id") or ""
                print("  newest session: %s (%s)" % (session_id, sessions[0].get("title", "")))
        if not session_id:
            print("  no sessions returned by the backend")
            return
        url = base + ("/api/sessions/%s/messages?limit=10&offset=0&order=latest&include_compacted=true"
                      % session_id)
        with urllib.request.urlopen(url, timeout=15) as resp:
            page = json.loads(resp.read().decode("utf-8", "replace"))
        rows = list(reversed(page.get("messages") or []))
        print("  rows returned: %d" % len(rows))
        for m in rows:
            role = m.get("role")
            content = m.get("content") if isinstance(m.get("content"), str) else ""
            display = m.get("display_content") if isinstance(m.get("display_content"), str) else ""
            shown = display or content
            flag_note = shown.lstrip().startswith(NOTE_PREFIXES)
            flag_img = "@image:" in shown
            print("  row=%s role=%s display=%s note=%s @image=%s" % (
                m.get("row_id"), role, "yes" if display else "no ",
                "YES" if flag_note else "-", "YES" if flag_img else "-"))
            print("      %s" % shown.replace("\r", "").replace("\n", "\\n")[:170])
        print()
        print("  how to read: NEW rows -> note=- @image=YES (fixed). OLD rows -> note=YES unless")
        print("  the shim's display projection is active (display=yes @image=YES on read).")
    except Exception as exc:
        print("  REST probe failed: %r" % (exc,))


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes Image Persist diagnostics (read-only)")
    parser.add_argument("--root", default="")
    parser.add_argument("--home", default="")
    parser.add_argument("--session", default="")
    parser.add_argument("--port", type=int, default=0)
    args = parser.parse_args()

    state = state_mod.load_state(discovery.hermes_home(args.home))
    home = discovery.hermes_home(args.home or state.get("home", ""))
    root = discovery.agent_root(home, args.root or state.get("root", ""))

    print("=== Hermes Image Persist 1.0.0 diagnostics (read-only) ===")
    if not root.is_dir():
        print("agent root not found: %s (pass --root)" % root)
        return 2

    section_layers(home, root)
    section_database(home, root)
    section_logs(home)
    section_rest(home, args.session, args.port)

    hr("VERDICT CHECKLIST")
    print("  A. shim.log shows 'durable image references set' for new Text turns?")
    print("  B. state.db newest user row: @image: + caption (IMG flag), not NOTE?")
    print("  C. REST row: display_content only on OLD note rows, never on new ones?")
    print("  D. Desktop keeps the thumbnail after the reply and after switching sessions?")
    print("  A/B missing but D ok      -> write path idle; check shim planting + guard status.")
    print("  A/B ok but D fails        -> projection layer; send diagnose output + desktop.log.")
    print("  status=needs_adaptation   -> source anchors changed; shim still covers new turns.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
