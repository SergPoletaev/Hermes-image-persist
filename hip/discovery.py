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
    """The interpreter of an environment (venv OR standalone Python root).

    v1.0.3: standalone Hermes tool Pythons keep ``python.exe`` in the ROOT
    (``tools\\python-3.14.7...\\python.exe``), not under ``Scripts\\``. v1.0.2
    only looked in ``Scripts/`` so those environments were planted fine but
    reported "no python in this environment" (UNVERIFIED) forever.
    """
    venv = Path(venv)
    if os.name == "nt":
        scripts = venv / "Scripts" / "python.exe"
        if scripts.is_file():
            return scripts
        return venv / "python.exe"          # standalone layout (tools/python-*)
    for name in ("python3", "python"):
        candidate = venv / "bin" / name
        if candidate.is_file():
            return candidate
    return venv / "bin" / "python3"


def committed_environment(home: Path, root: Path) -> Path | None:
    """The venv/Python PM committed for this install (facts.json), if any.

    v1.0.1: facts.json is looked up under EVERY ``installs/*/`` state dir, not
    only the crc32-keyed one for this root. If PM changed its keying scheme
    between Hermes versions (a plausible cause of the 1.0.0 field failure:
    the committed venv was never found, so the shim was never planted where
    the gateway actually runs), the environment is still discovered.
    """
    candidates = [install_state_dir(home, root) / "facts.json"]
    installs = hermes_home_disambiguated(home) / "installs"
    if installs.is_dir():
        try:
            for child in sorted(installs.iterdir()):
                if child.is_dir():
                    candidates.append(child / "facts.json")
        except Exception:
            pass
    for facts in candidates:
        value = _read_venv_from_facts(facts)
        if not value:
            continue
        env = Path(value)
        # Accept venvs (pyvenv.cfg) OR standalone Python roots (python.exe).
        if (env / "pyvenv.cfg").is_file() or _has_python_exe(env):
            return env
    return None


def _read_venv_from_facts(facts: Path):
    try:
        data = json.loads(facts.read_text(encoding="utf-8-sig"))
        value = data.get("packages", {}).get("venv", {}).get("environment")
    except Exception:
        return None
    return value if isinstance(value, str) and value else None


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

    # v1.0.1: interpreters of LIVE Hermes processes outrank everything below -
    # if the gateway is running on an environment the static search missed
    # (PM re-keyed its state dir, custom layout), this still finds it.
    for cmdline in live_hermes_commands():
        env = interpreter_env_from_command(cmdline)
        if env is not None:
            add(env, "liveprocess")

    state = install_state_dir(home, root)
    generations = state / "environments"
    if generations.is_dir():
        for child in sorted(generations.iterdir()):
            if child.is_dir():
                add(child, "generation")

    # v1.0.1: sibling state dirs may hold OTHER generations of the same install
    # (PM re-keying, multiple roots on one machine). Scan them too.
    installs = hermes_home_disambiguated(home) / "installs"
    if installs.is_dir():
        try:
            for child in sorted(installs.iterdir()):
                if not child.is_dir() or child == state:
                    continue
                envs = child / "environments"
                if envs.is_dir():
                    for grandchild in sorted(envs.iterdir()):
                        if grandchild.is_dir():
                            add(grandchild, "generation")
        except Exception:
            pass

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


def live_hermes_commands(marker: str = "hermes-agent") -> list:
    """Command lines of live Hermes processes (best-effort, never raises).

    Used to (a) plant the shim into the interpreter a live gateway process is
    ACTUALLY running on - the single most reliable signal there is - and (b)
    auto-derive the agent root from the command line when discovery missed it.
    Each entry is a raw command-line string.
    """
    try:
        if os.name == "nt":
            return _live_commands_windows(marker)
        if sys.platform == "darwin":
            return _live_commands_ps(marker)
        return _live_commands_proc(marker)
    except Exception:
        return []


# --------------------------------------------------------------------------
# v1.0.3: process TABLE (one query) + robust gateway detection.
#
# The v1.0.2 gateway detector asked PowerShell for command lines matching
# 'gateway.run'/'serve' - and PowerShell matched the QUERY ITSELF (its own
# command line contains those words), so installers saw themselves as
# gateways, killed ephemeral copies of themselves in a loop, and never
# touched the real one. The fix: fetch EVERY process (pid, ppid, cmdline,
# start time) once, then classify in Python with probe self-exclusion.
# --------------------------------------------------------------------------

_IS_GATEWAY_TOKENS = (
    "gateway.run",      # python -m gateway.run  (Windows + POSIX)
    "gateway\\run",     # Windows-path spelling of the same
    "gateway/run",      # POSIX-path spelling
    "-m gateway",       # module invocation prefix
    "tui_gateway",      # websocket server module
    "hermes serve",     # CLI spelling
    " serve",           # the Desktop's 'hermes.exe serve' backend verb
)

_PROBE_NAMES = ("powershell", "pwsh", "cmd.exe", "wmic")


def _snapshot_processes_windows() -> list:
    """[(pid, ppid, cmdline, name, start_epoch)] for every process (one CIM query).

    Probe self-exclusion: any probe shell whose OWN command line contains a
    gateway token is us (or a parent shell running us) - it gets pid=-1 so
    downstream classification can never select it. This is the exact bug that
    made v1.0.2's --restart-gateway hang killing its own probe children.
    """
    import json as _json
    import subprocess

    out = []
    try:
        query = ("Get-CimInstance Win32_Process | Select-Object "
                 "ProcessId,ParentProcessId,Name,CommandLine,CreationDate | "
                 "ConvertTo-Json -Compress")
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", query],
            capture_output=True, text=True, timeout=40,
            encoding="utf-8", errors="replace",
        )
        raw = (proc.stdout or "").strip()
        if not raw:
            return out
        rows = _json.loads(raw)
        if isinstance(rows, dict):
            rows = [rows]
        for row in rows:
            try:
                pid = int(row.get("ProcessId") or 0)
                ppid = int(row.get("ParentProcessId") or 0)
                name = str(row.get("Name") or "")
                cmdline = str(row.get("CommandLine") or "")
            except Exception:
                continue
            lowered = (name + " " + cmdline).lower()
            is_probe = pid != 0 and any(n in lowered for n in _PROBE_NAMES) \
                and any(t in lowered for t in _IS_GATEWAY_TOKENS)
            start = _parse_cim_date(str(row.get("CreationDate") or ""))
            out.append((-1 if is_probe else pid, ppid, cmdline, name, start))
    except Exception:
        pass
    return out


def _parse_cim_date(text: str):
    """CIM DateTime '20261118005843.500000+180' (or /Date(...)/) -> epoch, else None."""
    if not text:
        return None
    text = text.strip()
    try:
        if text.startswith("/Date("):                     # JSON-serialized WMI date
            return int(float(text[6:text.index(")")]) / 1000.0)   # milliseconds -> epoch
        base = text.split("+")[0].split("-")[0]
        if len(base) >= 14 and base[:14].isdigit():
            import calendar
            import time as _time

            st = _time.strptime(base[:14], "%Y%m%d%H%M%S")
            return calendar.timegm(st)
    except Exception:
        pass
    return None


def _snapshot_processes_proc() -> list:
    """POSIX /proc equivalent of _snapshot_processes_windows."""
    out = []
    try:
        boot = 0
        with open("/proc/stat", "r") as fh:
            boot = int(fh.readline().split()[1])
        hz = os.sysconf("SC_CLK_TCK")
        for entry in os.listdir("/proc"):
            if not entry.isdigit() or int(entry) == os.getpid():
                continue
            try:
                with open("/proc/%s/cmdline" % entry, "rb") as fh:
                    cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace").strip()
                with open("/proc/%s/stat" % entry, "r") as fh:
                    fields = fh.read().rsplit(") ", 1)[-1].split()
                ppid = int(fields[1])
                start = boot + int(fields[19]) // hz
                name = cmdline.split()[0] if cmdline else ""
                out.append((int(entry), ppid, cmdline, name, start))
            except Exception:
                continue
    except Exception:
        pass
    return out


def _snapshot_processes() -> list:
    try:
        if os.name == "nt":
            return _snapshot_processes_windows()
        return _snapshot_processes_proc()
    except Exception:
        return []


def _is_desktop_child(cmdline: str, name: str) -> bool:
    """Desktop window / its Chromium children - never the gateway to restart."""
    lowered = (cmdline or "").lower()
    if "--type=" in lowered:               # gpu-process / renderer / utility children
        return True
    if name.lower() == "hermes.exe" and " serve" not in lowered and "gateway" not in lowered:
        return True                        # the Electron shell without a serve verb
    return False                            # hermes.exe WITH 'serve' IS the gateway


def live_gateway_processes() -> list:
    """The REAL gateway processes, as a list of dicts.

    Each dict: {"pid", "ppid", "cmdline", "name", "start_epoch"}. Robust against
    the v1.0.2 self-matching bug: probe shells carry pid=-1 and are excluded
    here. Desktop/Electron children are excluded. A process counts as the
    gateway when its command line carries a gateway token, OR when it is a
    llama-server whose PARENT is a hermes/python process from this table (the
    gateway's supervised child - the strongest evidence a gateway is alive;
    seen in the field: llama-server respawned by the 22:53 gateway at 01:43).
    """
    table = _snapshot_processes()
    by_pid = {}
    for pid, ppid, cmdline, name, start in table:
        if pid > 0:
            by_pid[pid] = (ppid, cmdline, name, start)

    gateways = {}
    for pid, (ppid, cmdline, name, start) in by_pid.items():
        if _is_desktop_child(cmdline, name):
            continue
        lowered = (cmdline or "").lower()
        if any(token in lowered for token in _IS_GATEWAY_TOKENS):
            gateways[pid] = {"pid": pid, "ppid": ppid, "cmdline": cmdline,
                             "name": name, "start_epoch": start}

    # llama-server chain: its supervisor-parent IS the gateway even when the
    # parent's own command line is opaque (hermes.exe with no readable args).
    for pid, (ppid, cmdline, name, start) in by_pid.items():
        lowered_name = (name or "").lower() + " " + (cmdline or "").lower()
        if "llama-server" not in lowered_name and "llama_server" not in lowered_name:
            continue
        parent = by_pid.get(ppid)
        if not parent:
            continue
        parent_name = (parent[2] or "").lower()
        if "hermes" in parent_name or "python" in parent_name:
            # Mark the PARENT (the supervisor/gateway), not llama-server itself.
            if ppid not in gateways:
                gateways[ppid] = {"pid": ppid, "ppid": parent[0], "cmdline": parent[1],
                                  "name": parent[2], "start_epoch": parent[3],
                                  "via": "llama-server parent chain"}
    return sorted(gateways.values(), key=lambda g: g["start_epoch"] or 0)


def live_gateway_commands() -> list:
    """Command-line strings of the real gateway processes (v1.0.3 classifier)."""
    return [g["cmdline"] for g in live_gateway_processes() if g.get("cmdline")]


def gateway_restart_hint() -> str:
    """Human-readable hint for restarting the real gateway process."""
    return (
        "The Hermes GATEWAY process is still running with pre-fix code in memory "
        "(it survives Desktop restarts: it runs as a background 'serve' process). "
        "Fully exit Hermes from the tray (or Task Manager: end 'Hermes'/'hermes' "
        "python processes, NOT the llama-server) and start Hermes again.")


def process_start_epochs(cmdlines: list) -> dict:
    """Start epoch per lowercased command line, from the process table (v1.0.3).

    ``cmdlines`` is accepted for v1.0.2 call-site compatibility but ignored:
    the table snapshot is the source of truth (a bare command line is not a
    unique key - several processes can share one).
    """
    out = {}
    for pid, ppid, cmdline, name, start in _snapshot_processes():
        if cmdline and start:
            out.setdefault(cmdline.lower(), start)
    return out


def _live_commands_proc(marker: str) -> list:
    out = []
    for entry in os.listdir("/proc"):
        if not entry.isdigit():
            continue
        try:
            with open("/proc/%s/cmdline" % entry, "rb") as fh:
                cmdline = fh.read().replace(b"\x00", b" ").decode("utf-8", "replace")
        except Exception:
            continue
        if marker in cmdline and entry != str(os.getpid()):
            out.append(cmdline.strip())
    return out


def _live_commands_ps(marker: str) -> list:
    import subprocess

    out = subprocess.run(["ps", "-axo", "command="], capture_output=True, text=True,
                         timeout=15, encoding="utf-8", errors="replace")
    return [line.strip() for line in (out.stdout or "").splitlines()
            if marker in line and "ps -axo" not in line]


def _live_commands_windows(marker: str) -> list:
    import subprocess

    query = (
        "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*"
        + marker + "*' } | Select-Object -First 10 -ExpandProperty CommandLine"
    )
    out = subprocess.run(
        ["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", query],
        capture_output=True, text=True, timeout=40,
        encoding="utf-8", errors="replace",
    )
    return [line.strip() for line in (out.stdout or "").splitlines() if line.strip()]


def interpreter_env_from_command(cmdline: str) -> Path | None:
    """The Python environment (venv root or standalone root) a command line runs on.

    Accepts ``<...>/python.exe|-m gateway ...`` / ``python -I script ...`` shapes;
    resolves ``..\\Scripts\\python.exe`` / ``../bin/python3`` to the env root.
    Returns None when the executable does not look like a Python launcher.
    """
    if not cmdline:
        return None
    tokens = cmdline.replace("\\", "/").split()
    exe = ""
    for token in tokens:
        lowered = token.lower()
        if lowered.endswith("/python.exe") or lowered.endswith("/pythonw.exe") \
                or lowered.endswith("/python") or lowered.endswith("/python3") \
                or lowered.endswith("/python3.exe") or lowered.endswith("/hermes.exe"):
            if lowered.endswith("/hermes.exe") and not exe:
                # The tree launcher is itself a Python shim; its own dir is the env.
                path = Path(token)
                return _env_root_for(path.parent)
            exe = token
            break
    if not exe:
        return None
    return _env_root_for(Path(exe))


def _env_root_for(exe: Path) -> Path | None:
    """Env root for a python executable: Scripts/ or bin/ parent, else the dir itself."""
    try:
        exe = Path(exe)
        if exe.parent.name.lower() in ("scripts", "bin"):
            return exe.parent.parent
        if exe.name.lower().startswith("python") or exe.name.lower() == "hermes.exe":
            return exe.parent
    except Exception:
        return None
    return None


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
