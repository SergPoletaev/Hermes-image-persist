"""State file (``<home>/hermes-image-persist/state.json``).

The state dir is deliberately OUTSIDE ``hermes-agent`` so Hermes updates never
wipe it; the guard uses it to remember what was done and where the install lives.

Note: the previous sha256/fingerprint change-detection machinery was removed.
It was redundant (the guard already detects a needed patch via plan_all()) and
it produced false "tree changed" signals whenever any file under the agent root
was touched, which broke the guard pass on minor edits. The guard now simply
re-plants the shim and re-applies the source patch whenever plan_all() reports
that one is needed - no content hashing involved.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path


def state_dir(home: Path) -> Path:
    return Path(home) / "hermes-image-persist"


def backups_dir(home: Path) -> Path:
    return state_dir(home) / "backups"


def state_path(home: Path) -> Path:
    return state_dir(home) / "state.json"


def load_state(home: Path) -> dict:
    try:
        data = json.loads(state_path(home).read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def save_state(home: Path, state: dict) -> None:
    directory = state_dir(home)
    directory.mkdir(parents=True, exist_ok=True)
    state["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    tmp = state_path(home).with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, state_path(home))


def append_log(home: Path, message: str, name: str = "guard.log") -> None:
    try:
        directory = state_dir(home)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / name
        if path.is_file() and path.stat().st_size > 1_048_576:
            os.replace(path, path.with_suffix(".1"))
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write("%s %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), message))
    except Exception:
        pass
