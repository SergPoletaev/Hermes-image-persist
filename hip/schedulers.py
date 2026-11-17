"""OS-level persistence for the guard (layer 3).

Registers an hourly + at-logon maintenance run so the fix re-installs itself
after Hermes updates (source tree replaced, new PM dependency generation, or
a rebuilt venv):

* Windows: Scheduled Task ``HermesImagePersistGuard`` (schtasks, user scope).
* macOS:   LaunchAgent ``com.hermes.image-persist-guard.plist``.
* Linux:   systemd user unit + timer, with a user-crontab fallback.

Everything is best-effort and idempotent; each function returns
``(ok, detail)``.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

TASK_NAME = "HermesImagePersistGuard"
LABEL = "com.hermes.image-persist.guard"
UNIT = "hermes-image-persist-guard"


def _python_for_task() -> str:
    """Prefer pythonw.exe on Windows (no console window), else this interpreter."""
    if os.name == "nt":
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        if pythonw.is_file():
            return str(pythonw)
    return sys.executable


def guard_command(repo_dir: Path) -> list:
    return [_python_for_task(), str(Path(repo_dir) / "guard.py"), "--quiet"]


# ---------------------------------------------------------------------------
# windows
# ---------------------------------------------------------------------------

def register_windows(repo_dir: Path) -> tuple:
    try:
        exe, script = _python_for_task(), str(Path(repo_dir) / "guard.py")
        # One task, two triggers: hourly repeat + run at logon.
        for trigger in ("/SC HOURLY /MO 1", "/SC ONLOGON"):
            cmd = [
                "schtasks.exe", "/Create", "/F", "/TN", TASK_NAME + ("" if trigger.startswith("/SC HOURLY") else "AtLogon"),
                "/TR", '"%s" "%s" --quiet' % (exe, script),
            ] + trigger.split() + ["/RL", "LIMITED"]
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            if out.returncode != 0:
                return False, "schtasks failed: %s" % (out.stderr or out.stdout).strip()[:200]
        return True, "tasks %s, %sAtLogon registered" % (TASK_NAME, TASK_NAME)
    except Exception as exc:
        return False, "registration failed: %r" % (exc,)


def unregister_windows() -> tuple:
    try:
        ok = True
        details = []
        for name in (TASK_NAME, TASK_NAME + "AtLogon"):
            out = subprocess.run(["schtasks.exe", "/Delete", "/F", "/TN", name],
                                 capture_output=True, text=True, timeout=30)
            details.append("%s:%d" % (name, out.returncode))
            if out.returncode != 0 and "cannot find" not in (out.stderr or "").lower():
                ok = False
        return ok, ", ".join(details)
    except Exception as exc:
        return False, "unregister failed: %r" % (exc,)


# ---------------------------------------------------------------------------
# macos
# ---------------------------------------------------------------------------

def _launch_agent_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / (LABEL + ".plist")


def register_macos(repo_dir: Path) -> tuple:
    try:
        exe, script = _python_for_task(), str(Path(repo_dir) / "guard.py")
        plist = _launch_agent_path()
        plist.parent.mkdir(parents=True, exist_ok=True)
        body = """<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{exe}</string>
        <string>{script}</string>
        <string>--quiet</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>StartInterval</key>
    <integer>3600</integer>
    <key>StandardOutPath</key>
    <string>{log}</string>
    <key>StandardErrorPath</key>
    <string>{log}</string>
</dict>
</plist>
""".format(label=LABEL, exe=exe, script=script,
           log=str(Path.home() / "Library" / "Logs" / (LABEL + ".log")))
        plist.write_text(body, encoding="utf-8")
        for cmd in (
            ["launchctl", "unload", str(plist)],
            ["launchctl", "load", str(plist)],
        ):
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        return True, "LaunchAgent %s loaded (hourly + at login)" % LABEL
    except Exception as exc:
        return False, "registration failed: %r" % (exc,)


def unregister_macos() -> tuple:
    try:
        plist = _launch_agent_path()
        subprocess.run(["launchctl", "unload", str(plist)], capture_output=True, timeout=30)
        if plist.is_file():
            plist.unlink()
        return True, "LaunchAgent removed"
    except Exception as exc:
        return False, "unregister failed: %r" % (exc,)


# ---------------------------------------------------------------------------
# linux
# ---------------------------------------------------------------------------

def _systemd_dir() -> Path:
    return Path.home() / ".config" / "systemd" / "user"


def register_linux(repo_dir: Path) -> tuple:
    exe, script = _python_for_task(), str(Path(repo_dir) / "guard.py")
    unit_dir = _systemd_dir()
    service = """[Unit]
Description=Hermes Image Persist guard (re-apply fix after Hermes updates)

[Service]
Type=oneshot
ExecStart={exe} {script} --quiet
""".format(exe=exe, script=script)
    timer = """[Unit]
Description=Run the Hermes Image Persist guard hourly and at login

[Timer]
OnBootSec=2min
OnStartupSec=2min
OnUnitActiveSec=1h
Persistent=true

[Install]
WantedBy=timers.target
"""
    try:
        unit_dir.mkdir(parents=True, exist_ok=True)
        (unit_dir / (UNIT + ".service")).write_text(service, encoding="utf-8")
        (unit_dir / (UNIT + ".timer")).write_text(timer, encoding="utf-8")
        out = subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True, timeout=30)
        if out.returncode == 0:
            subprocess.run(["systemctl", "--user", "enable", "--now", UNIT + ".timer"],
                           capture_output=True, text=True, timeout=30)
            return True, "systemd user timer %s enabled" % UNIT
        return _register_cron(script, exe)
    except Exception:
        return _register_cron(script, exe)


def _register_cron(script: str, exe: str) -> tuple:
    try:
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=15)
        existing = current.stdout if current.returncode == 0 else ""
        line = "@hourly %s %s --quiet" % (exe, script)
        if line in existing:
            return True, "cron entry already present"
        new = (existing.rstrip("\n") + "\n" + line + "\n").lstrip("\n")
        proc = subprocess.run(["crontab", "-"], input=new, capture_output=True, text=True, timeout=15)
        if proc.returncode == 0:
            return True, "user crontab @hourly entry installed (systemd unavailable)"
        return False, "crontab install failed: %s" % (proc.stderr or "").strip()[:200]
    except Exception as exc:
        return False, "cron registration failed: %r" % (exc,)


def unregister_linux() -> tuple:
    details = []
    try:
        unit_dir = _systemd_dir()
        subprocess.run(["systemctl", "--user", "disable", "--now", UNIT + ".timer"],
                       capture_output=True, timeout=30)
        for name in (UNIT + ".service", UNIT + ".timer"):
            path = unit_dir / name
            if path.is_file():
                path.unlink()
                details.append("removed " + name)
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=30)
    except Exception:
        pass
    try:
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=15)
        if current.returncode == 0:
            lines = [ln for ln in current.stdout.splitlines() if "guard.py" not in ln]
            subprocess.run(["crontab", "-"], input="\n".join(lines) + ("\n" if lines else ""),
                           capture_output=True, text=True, timeout=15)
            details.append("cron cleaned")
    except Exception:
        pass
    return True, "; ".join(details) or "nothing to remove"


# ---------------------------------------------------------------------------
# dispatch
# ---------------------------------------------------------------------------

def register(repo_dir: Path) -> tuple:
    if os.name == "nt":
        return register_windows(repo_dir)
    if sys.platform == "darwin":
        return register_macos(repo_dir)
    return register_linux(repo_dir)


def unregister() -> tuple:
    if os.name == "nt":
        return unregister_windows()
    if sys.platform == "darwin":
        return unregister_macos()
    return unregister_linux()


def registered() -> str:
    """Human-readable registration status (never raises)."""
    try:
        if os.name == "nt":
            out = subprocess.run(["schtasks.exe", "/Query", "/TN", TASK_NAME],
                                 capture_output=True, text=True, timeout=20)
            return "scheduled task present" if out.returncode == 0 else "not registered"
        if sys.platform == "darwin":
            return "launch agent present" if _launch_agent_path().is_file() else "not registered"
        timer = _systemd_dir() / (UNIT + ".timer")
        if timer.is_file():
            return "systemd timer present"
        current = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=15)
        if current.returncode == 0 and "guard.py" in current.stdout:
            return "cron entry present"
        return "not registered"
    except Exception as exc:
        return "unknown (%r)" % (exc,)
