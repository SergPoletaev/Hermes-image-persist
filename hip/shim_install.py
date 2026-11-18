"""Runtime-shim installation (layer 1) - the update-immune part of the fix.

Plants ``hermes_image_persist.py`` + a ``.pth`` line into the site-packages
of every Python environment the gateway can run on. ``hermes_bootstrap`` puts
that site-packages on ``sys.path`` via ``site.addsitedir()`` at every process
start, which executes ``.pth`` import lines - so the shim is active in every
Hermes process regardless of how the launcher was built (``python -I`` still
imports ``site``; only the user-site is disabled, not the venv site-packages).

v1.0.1: after planting, the shim is VERIFIED inside the target environment
(``<env-python> -I -c "import hermes_image_persist"``). A .pth file can be
present on disk but never executed by the interpreter that actually runs the
gateway (wrong site-packages dir for that interpreter, per-user site
isolation, a broken .pth) - 1.0.0 reported "planted" in exactly that
situation while the gateway ran unpatched. Now every environment is verified
and diagnostics surface a silent shim immediately.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import discovery, payload_source

SHIM_MODULE = "hermes_image_persist"
PTH_NAME = "hermes-image-persist.pth"

# Earlier private builds planted versioned file names; they are removed
# whenever we touch the same environment (upgrade cleanup).
LEGACY_MODULES = ("hermes_image_persist_v9",)
LEGACY_PTHS = ("hermes-image-persist-v9.pth",)
LEGACY_TMP_SUFFIXES = (".py.hipv9.tmp",)

VERIFY_CODE = (
    "import sys\n"
    "import %s as shim\n"
    "print('OK %%s %%s' %% (shim.__version__, getattr(sys, '_hermes_image_persist_installed', False)))\n"
) % SHIM_MODULE


def pth_line() -> str:
    return "import %s\n" % SHIM_MODULE


def shim_paths_for_venv(venv: Path) -> tuple:
    sp = discovery.site_packages(venv)
    return sp / (SHIM_MODULE + ".py"), sp / PTH_NAME


def shim_installed(venv: Path) -> bool:
    module, pth = shim_paths_for_venv(venv)
    return module.is_file() and pth.is_file()


def verify_shim(venv: Path, timeout: int = 30) -> tuple:
    """Run the env's own python -I and prove the shim imports + installs its hook.

    Returns ``(verified, detail)``. Never raises. ``-I`` mirrors the Hermes
    launchers (isolated mode still executes .pth lines from the venv's
    site-packages - the exact mechanism hermes_bootstrap relies on).
    """
    python = discovery.venv_python(venv)
    if not python.is_file():
        return False, "no python in this environment (%s)" % python
    try:
        proc = subprocess.run(
            [str(python), "-I", "-c", VERIFY_CODE],
            capture_output=True, text=True, timeout=timeout,
            encoding="utf-8", errors="replace",
        )
    except Exception as exc:
        return False, "verify failed to run: %r" % (exc,)
    out = (proc.stdout or "").strip().splitlines()
    if proc.returncode == 0 and out and out[-1].startswith("OK "):
        parts = out[-1].split(" ", 2)
        hook_flag = parts[2].strip() if len(parts) > 2 else ""
        if hook_flag == "True":
            return True, "shim imports and installs its hook (v%s)" % parts[1]
        return False, "shim imports but hook NOT installed (disabled by env?)"
    return False, "shim did not import: %s" % ((proc.stderr or proc.stdout or "")[:160].strip())


def _remove_legacy(sp: Path) -> int:
    """Delete files left behind by earlier private builds. Returns count."""
    removed = 0
    targets = [sp / (name + ".py") for name in LEGACY_MODULES]
    targets += [sp / name for name in LEGACY_PTHS]
    for module in LEGACY_MODULES:
        targets += [sp / (module + suffix) for suffix in LEGACY_TMP_SUFFIXES]
    for path in targets:
        try:
            if path.is_file():
                path.unlink()
                removed += 1
        except Exception:
            pass
    return removed


def install_shim(venv: Path, verify: bool = True) -> tuple:
    """Plant shim + .pth into one venv. Returns ``(installed, detail)``.

    v1.0.1: when ``verify`` is set and the environment has its own python,
    the shim is proven to import + install its hook from THAT interpreter
    before we report success. ``installed`` stays True on a silent shim, but
    the detail says ``UNVERIFIED`` so installers/diagnostics can flag it.
    """
    module, pth = shim_paths_for_venv(venv)
    try:
        module.parent.mkdir(parents=True, exist_ok=True)
        _remove_legacy(module.parent)
        source = payload_source.shim_source()
        current = module.read_text(encoding="utf-8") if module.is_file() else ""
        if current != source:
            tmp = module.with_suffix(".py.hip.tmp")
            tmp.write_text(source, encoding="utf-8", newline="")
            tmp.replace(module)
        if not pth.is_file() or pth.read_text(encoding="utf-8").strip() != pth_line().strip():
            pth.write_text(pth_line(), encoding="utf-8", newline="")
        detail = "planted into %s" % module.parent
        if verify:
            ok, vdetail = verify_shim(venv)
            if ok:
                detail += "; " + vdetail
            else:
                detail += "; UNVERIFIED: %s" % vdetail
        return True, detail
    except Exception as exc:
        return False, "failed for %s: %r" % (module.parent, exc)


def install_shim_everywhere(home: Path, root: Path, venvs=None, verify: bool = True) -> list:
    """Plant the shim into every discovered environment. Returns results list."""
    results = []
    for venv, kind in (venvs if venvs is not None else discovery.discover_venvs(home, root)):
        ok, detail = install_shim(venv, verify=verify)
        results.append({"venv": str(venv), "kind": kind, "ok": ok, "detail": detail,
                        "unverified": "UNVERIFIED" in detail})
    return results


def remove_shim(venv: Path) -> bool:
    module, pth = shim_paths_for_venv(venv)
    removed = False
    candidates = [module, pth, module.with_suffix(".py.hip.tmp")]
    sp = module.parent
    candidates += [sp / (name + ".py") for name in LEGACY_MODULES]
    candidates += [sp / name for name in LEGACY_PTHS]
    for module in LEGACY_MODULES:
        candidates += [sp / (module + suffix) for suffix in LEGACY_TMP_SUFFIXES]
    for path in candidates:
        try:
            if path.is_file():
                path.unlink()
                removed = True
        except Exception:
            pass
    return removed


def remove_shim_everywhere(home: Path, root: Path, recorded=None) -> list:
    removed = []
    venvs = []
    if recorded:
        venvs = [Path(entry) for entry in recorded if Path(entry).is_dir()]
    for venv, _kind in discovery.discover_venvs(home, root):
        if venv not in venvs:
            venvs.append(venv)
    for venv in venvs:
        if remove_shim(venv):
            removed.append(str(venv))
    return removed
