"""Discovery of the Hermes install layout, Python environments and version.

Supported layouts (all best-effort, nothing is created here):

- Hermes home: ``HERMES_HOME`` env, else ``%LOCALAPPDATA%/hermes`` (Windows)
  or ``~/.hermes`` (Linux/macOS) - mirrors ``hermes_constants``.
- Agent root: ``<home>/hermes-agent`` by default; any ``--root`` override wins.
- Python environments that can run the gateway:
  * the PM "committed generation" venv recorded in
    ``<home>/installs/<key>/facts.json`` (``packages.venv.environment``),
  * every sibling generation under ``<home>/installs/<key>/environments/``,
  * legacy in-tree ``<agent>/venv`` / ``<agent>/.venv``,
  * out-of-tree ``<home>/venvs/*``,
  * the interpreter running the installer, when it is a venv.

``install_key`` mirrors ``pm.environments``: a short deterministic key
derived from the agent_root path (crc32, 8 hex chars). It is only used to
namespace the install-state directory - it is NOT a file-content checksum
and never participates in change detection.
"""
from __future__ import annotations

import json
import os
import sys
import zlib
from pathlib import Path


def hermes_home(explicit: str = "") -> Path:
    if explicit:
        return Path(explicit).expanduser()
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return Path(env).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA", "").strip()
        if not base:
            base = str(Path.home() / "AppData" / "Local")
        return Path(base) / "hermes"
    return Path.home() / ".hermes"


def agent_root(home: Path, explicit: str = "") -> Path:
    if explicit:
        return Path(explicit).expanduser()
    return Path(home) / "hermes-agent"


def install_key(root: Path) -> str:
    """Short deterministic key for the agent root path (crc32, 8 hex chars).

    Used only to namespace the install-state directory under <home>/installs/.
    Not a file-content hash - it never changes when files under the tree are
    edited, only when the agent root path itself changes.
    """
    canonical = str(Path(root).resolve())
    return "%08x" % (zlib.crc32(canonical.encode("utf-8")) & 0xffffffff)


def install_state_dir(home: Path, root: Path) -> Path:
    return hermes_home_disambiguated(home) / "installs" / install_key(root)


def hermes_home_disambiguated(home: Path) -> Path:
    """The home that owns dependency state (profiles may nest one level)."""
    return Path(home)


def read_stamp(root: Path) -> dict:
    """``install-stamp.json`` of the agent tree (displayVersion/commit), best-effort."""
    path = Path(root) / "install-stamp.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def version_string(root: Path) -> str:
    stamp = read_stamp(root)
    version = stamp.get("displayVersion") or stamp.get("baseVersion") or ""
    commit = (stamp.get("commit") or "")[:7]
    if version and commit:
        return "%s (%s)" % (version, commit)
    return version or commit or "unknown"


def site_packages(venv: Path) -> Path:
    """Site-packages of a venv, mirroring ``pm.environments.site_packages``."""
    venv = Path(venv)
    if os.name == "nt":
        return venv / "Lib" / "site-packages"
    version = _venv_python_version(venv)
    if version is None:
        version = (sys.version_info.major, sys.version_info.minor)
    return venv / ("lib/python%d.%d/site-packages" % version)


def _venv_python_version(venv: Path):
    try:
        for line in (venv / "pyvenv.cfg").read_text(encoding="utf-8-sig").splitlines():
            key, _, value = line.partition("=")
            if key.strip() not in ("version", "version_info"):
                continue
            major, _, rest = value.strip().partition(".")
            minor = rest.partition(".")[0]
            if major.isdigit() and minor.isdigit():
                return int(major), int(minor)
    except Exception:
        pass
    try:
        candidates = sorted((venv / "lib").glob("python3*"))
    except Exception:
        candidates = []
    for candidate in candidates:
        major, _, rest = candidate.name.removeprefix("python").partition(".")
        minor = rest.partition(".")[0]
        if major.isdigit() and minor.isdigit():
            return int(major), int(minor)
    return None


def venv_python(venv: Path) -> Path:
    venv = Path(venv)
    if os.name == "nt":
        return venv / "Scripts" / "python.exe"
    return venv / "bin" / "python3"


def committed_environment(home: Path, root: Path) -> Path | None:
    """The venv/Python PM committed for this install (facts.json), if any."""
    facts = install_state_dir(home, root) / "facts.json"
    try:
        data = json.loads(facts.read_text(encoding="utf-8-sig"))
        value = data.get("packages", {}).get("venv", {}).get("environment")
    except Exception:
        return None
    if not isinstance(value, str) or not value:
        return None
    env = Path(value)
    # Accept venvs (pyvenv.cfg) OR standalone Python roots (python.exe).
    if not (env / "pyvenv.cfg").is_file() and not _has_python_exe(env):
        return None
    return env


def _has_python_exe(path: Path) -> bool:
    """True if the directory looks like a Python root (venv or standalone)."""
    path = Path(path)
    if os.name == "nt":
        return (path / "python.exe").is_file()
    return (path / "bin" / "python3").is_file() or (path / "bin" / "python").is_file()


def discover_venvs(home: Path, root: Path, include_running: bool = True) -> list:
    """All Python environments the gateway could run on, most-authoritative first.

    Returns a list of ``(env_path, kind)`` tuples; ``kind`` is one of
    ``committed``, ``generation``, ``intree``, ``outtree``, ``standalone``,
    ``running``.

    Both real venvs (directories with ``pyvenv.cfg``) and standalone Python
    installations (directories with ``python.exe`` / ``bin/python3`` but no
    ``pyvenv.cfg``) are accepted. Hermes ships a standalone interpreter under
    ``<home>/tools/`` (e.g. ``python-3.14.x-win32-x64``) — without this, the
    shim would never be planted into the interpreter Hermes actually uses.
    """
    found = []
    seen = set()

    def add(path, kind):
        try:
            path = Path(path).resolve()
        except Exception:
            return
        if path in seen:
            return
        # Accept venvs (pyvenv.cfg) OR standalone Python roots (python.exe).
        if not (path / "pyvenv.cfg").is_file() and not _has_python_exe(path):
            return
        seen.add(path)
        found.append((path, kind))

    committed = committed_environment(home, root)
    if committed is not None:
        add(committed, "committed")

    state = install_state_dir(home, root)
    generations = state / "environments"
    if generations.is_dir():
        for child in sorted(generations.iterdir()):
            if child.is_dir():
                add(child, "generation")

    for name in ("venv", ".venv"):
        candidate = Path(root) / name
        if candidate.is_dir():
            add(candidate, "intree")

    venvs = hermes_home_disambiguated(home) / "venvs"
    if venvs.is_dir():
        for child in sorted(venvs.iterdir()):
            if child.is_dir():
                add(child, "outtree")

    # Standalone Python shipped under <home>/tools/ (e.g. python-3.14.x-win32-x64).
    # This is where Hermes bundles its interpreter on Windows.
    tools_dir = Path(home) / "tools"
    if tools_dir.is_dir():
        for child in sorted(tools_dir.iterdir()):
            if child.is_dir() and _has_python_exe(child):
                add(child, "standalone")

    if include_running:
        prefix = Path(sys.prefix)
        # Include the running interpreter's prefix if it looks Hermes-related
        # OR is a venv (sys.prefix != sys.base_prefix).
        plausible = (
            "hermes" in str(prefix).lower()
            or str(prefix).startswith(str(Path(home)))
            or sys.prefix != sys.base_prefix
        )
        if plausible:
            add(prefix, "running")

    return found


def hermes_process_running(root: Path) -> tuple:
    """``(running, detail)`` - best-effort detection of live Hermes processes.

    Never raises. On detection failure returns ``(False, reason)`` so callers
    proceed with the source patch when we cannot prove Hermes is live - the
    runtime shim already covers the live case, and a false "running" signal
    would block the source patch indefinitely (the original cause of the
    guard stalls on CI runners and after minor file edits).
    """
    root_marker = "hermes-agent"
    try:
        if os.name == "nt":
            return _processes_windows(root_marker)
        if sys.platform == "darwin":
            return _processes_ps(root_marker)
        return _processes_proc(root_marker)
    except Exception as exc:
        return False, "detection failed (assuming stopped): %r" % (exc,)


def _processes_proc(marker: str) -> tuple:
    pids = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % entry, "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except Exception:
            continue
        if marker in cmdline and entry != str(os.getpid()):
            pids.append((entry, cmdline.strip()[:160]))
    if pids:
        return True, "; ".join("%s: %s" % p for p in pids[:4])
    return False, "no live hermes processes found (/proc scan)"


def _processes_ps(marker: str) -> tuple:
    import subprocess

    out = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True,
                        timeout=15, encoding="utf-8", errors="replace")
    hits = []
    for line in out.stdout.splitlines():
        line = line.strip()
        if marker in line and "ps -axo" not in line:
            hits.append(line[:160])
    if hits:
        return True, "; ".join(hits[:4])
    return False, "no live hermes processes found (ps scan)"


def _processes_windows(marker: str) -> tuple:
    import subprocess

    query = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*"
        + marker + "*' } | Select-Object -First 6 ProcessId, Name | ConvertTo-Json -Compress"
    )
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", query],
        capture_output=True, text=True, timeout=40,
        encoding="utf-8", errors="replace",
    )
    payload = (out.stdout or "").strip()
    if not payload:
        return False, "no live hermes processes found (CIM scan)"
    try:
        data = json.loads(payload)
    except Exception:
        data = None
    if data:
        rows = data if isinstance(data, list) else [data]
        detail = "; ".join("%s %s" % (r.get("Name"), r.get("ProcessId")) for r in rows if isinstance(r, dict))
        return True, detail or payload[:160]
    return False, "no live hermes processes found (CIM scan)"
