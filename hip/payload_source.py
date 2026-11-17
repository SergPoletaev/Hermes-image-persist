"""Access to the shipped payload files (kept verbatim, no code generation)."""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PAYLOAD_DIR = REPO_ROOT / "payload"
SHIM_SOURCE = REPO_ROOT / "hermes_image_persist.py"
HELPER_NAME = "local_image_persist.py"


def helper_source() -> str:
    return (PAYLOAD_DIR / HELPER_NAME).read_text(encoding="utf-8")


def shim_source() -> str:
    return SHIM_SOURCE.read_text(encoding="utf-8")
