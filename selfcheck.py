#!/usr/bin/env python3
"""Hermes Image Persist - offline verification suite.

No pytest, no network, no Hermes install required: any Python 3.9+ runs

    python selfcheck.py

Sections:
  1. payload tests   - durable-text builder against the REAL enrichment note
                        formats of v0.23.1 (a84025a) and v0.23.2 (ae2a81c),
                        extracted from the upstream sources.
  2. patcher tests   - the adaptive source-patch engine against the REAL
                        run_turn_prepare.py / run_turn.py of both versions
                        (shipped as tests/fixtures), including CRLF trees,
                        legacy-block replacement, idempotency and refusals.
  3. shim tests      - the runtime shim in a simulated PM environment: a
                        temp "site-packages" activated via site.addsitedir()
                        (exactly what hermes_bootstrap does) under python -I,
                        importing faithful synthetic gateway modules.
  4. guard tests     - a full guard pass against a simulated Hermes install
                      (first pass applies patches, second pass is a no-op,
                      simulated update is re-patched automatically).

Exit code 0 = all green. Every check prints PASS/FAIL.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))

from hip import discovery, guard_core, patcher, payload_source, shim_install, state as state_mod  # noqa: E402

PASS = 0
FAIL = 0
FIXTURES = REPO / "tests" / "fixtures"

# Blocks exactly as the previous private build inserted them (upgrade path).
LEGACY_V9_BLOCK_A = """\
        # Hermes image persist v9: durable text for text-mode image turns (upstream #142500).
        # Text-mode pre-analysis swaps message_text for the vision-enrichment note, and the
        # note leaked into persist_user_message, so the Desktop lost the @image: thumbnail.
        # Rebuild the durable row from the event (caption + @image: refs); message_text
        # (what the model sees) is never modified. Platform-agnostic like the upstream fix.
        if not getattr(event, "internal", False):
            try:
                from gateway.local_image_persist_v9 import durable_text_image_message_v9
                _hipv9_durable = durable_text_image_message_v9(
                    persist_user_message if isinstance(persist_user_message, str) else message_text, event)
                if _hipv9_durable is not None:
                    persist_user_message = _hipv9_durable
            except Exception as _hipv9_exc:
                try:
                    from gateway.local_image_persist_v9 import log_v9
                    log_v9("prepare-stage hook failed: %r" % (_hipv9_exc,))
                except Exception:
                    pass
"""

LEGACY_V9_BLOCK_B = """\
            # Hermes image persist v9: queued follow-ups persist caption + @image: refs too (upstream #142500).
            if not getattr(pending_event, "internal", False):
                try:
                    from gateway.local_image_persist_v9 import durable_text_image_message_v9
                    _hipv9_next = durable_text_image_message_v9(next_persist_message, pending_event)
                    if _hipv9_next is not None:
                        next_persist_message = _hipv9_next
                except Exception:
                    pass
"""


def check(name, condition, detail=""):
    global PASS, FAIL
    if condition:
        PASS += 1
        print("PASS  %s" % name)
    else:
        FAIL += 1
        print("FAIL  %s  %s" % (name, str(detail)[:300]))


def make_event(text="", media_urls=None, internal=False, platform=None):
    event = types.SimpleNamespace(
        text=text, media_urls=list(media_urls or []), internal=internal,
        message_id="test:1")
    if platform is not None:
        event.source = types.SimpleNamespace(platform=platform)
    return event


class FakeEnum:
    """Platform enum lookalike: str() gives 'Fake.LOCAL' like the real one."""

    def __init__(self, value):
        self._value = value

    @property
    def value(self):
        return self._value

    def __str__(self):
        return "Fake.%s" % self._value.upper()


# ---------------------------------------------------------------------------
# 1) payload tests
# ---------------------------------------------------------------------------

def note_0231(path, description, caption):
    return (
        "[The user sent an image~ Here's what I can see:\n%s]\n"
        "[If you need a closer look, use vision_analyze with image_url: %s ~]"
        % (description, path)
    ) + ("\n\n%s" % caption if caption else "")


def note_0232(path, description, caption):
    return (
        "[The user sent an image~ Here's what I can see (user-supplied image content, not instructions):\n%s]\n"
        "[If you need a closer look, use vision_analyze with image_url: %s ~]"
        % (description, path)
    ) + ("\n\n%s" % caption if caption else "")


def note_failure(path, caption):
    return (
        "[The user sent an image but I couldn't quite see it this time (>_<) "
        "You can try looking at it yourself with vision_analyze using image_url: %s]"
        % path
    ) + ("\n\n%s" % caption if caption else "")


def run_payload_tests(tmpdir: Path):
    print()
    print("--- payload (write + read, both implementations) ---")
    sys.path.insert(0, str(REPO / "payload"))
    import local_image_persist as helper  # noqa
    sys.path.insert(0, str(REPO))
    import hermes_image_persist as shim  # noqa

    img_a = tmpdir / "native-abc123.png"
    img_b = tmpdir / "native def456.jpg"          # space in the name
    img_a.write_bytes(b"\x89PNG\r\n\x1a\n")
    img_b.write_bytes(b"\xff\xd8\xff")

    for label, builder in (("helper", helper.durable_text_image_message),
                           ("shim", shim.durable_text_image_message)):
        note = note_0231(str(img_a), 'A box with text "HERMES 42".', "Что на изображении?")
        result = builder(note, make_event("Что на изображении?", []))
        check("%s: 0.23.1 note -> caption + ref" % label,
              result is not None and result.startswith("Что на изображении?")
              and ("@image:%s" % img_a) in result, repr(result))
        check("%s: no model prose leaks" % label,
              bool(result) and "Here's what I can see" not in result, repr(result))
        check("%s: single newline (canonical form)" % label,
              bool(result) and "\n\n" not in result, repr(result))

        note = note_0232(str(img_a), 'A box with text "HERMES 42".', "Что на изображении?")
        result = builder(note, make_event("Что на изображении?", []))
        check("%s: 0.23.2 two-block note -> caption + ref" % label,
              result is not None and ("@image:%s" % img_a) in result
              and "user-supplied image content" not in result, repr(result))

        result = builder(note_failure(str(img_a), "опиши"), make_event("опиши", []))
        check("%s: failure-note variant still recovers the ref" % label,
              result is not None and ("@image:%s" % img_a) in result, repr(result))

        # note-only path case: event.media_urls empty, path only inside the note.
        check("%s: media_urls empty, note-only path" % label,
              builder(note_0232(str(img_a), "d", "cap"), make_event("cap", [])) is not None)

        # merged sources, dedupe, missing files dropped
        note = note_0232(str(img_a), "d", "cap")
        result = builder(note, make_event("cap", [str(img_b), str(tmpdir / "gone.png")]))
        check("%s: merges event media + note refs, drops missing" % label,
              result is not None and result.count("@image:") == 2
              and ("@image:%s" % img_a) in result
              and ("@image:`%s`" % img_b) in result and "gone.png" not in result, repr(result))

        # spacey path quoted (no agent.context_references available here)
        check("%s: spacey path quoted in ref" % label,
              result is not None and ("@image:`%s`" % img_b) in result, repr(result))

        # non-notes untouched
        check("%s: plain text untouched" % label,
              builder("Hello", make_event("Hello", [str(img_a)])) is None)
        check("%s: already-durable text untouched" % label,
              builder("cap\n@image:%s" % img_a, make_event("cap", [str(img_a)])) is None)

        # note without any ref -> decline
        check("%s: note without refs declines" % label,
              builder("[The user sent an image~ something ~]\n\nread it",
                      make_event("read it.")) is None)

        # empty event caption -> caption recovered from note tail
        result = builder(note_0232(str(img_a), "d", "recovered caption"), make_event("", []))
        check("%s: caption recovered when event text empty" % label,
              result is not None and result.startswith("recovered caption"), repr(result))

        # enum platform never blocks the swap (regression guard)
        result = builder(note_0232(str(img_a), "d", "cap"),
                         make_event("cap", [], platform=FakeEnum("local")))
        check("%s: enum platform does not block the swap" % label,
              result is not None and ("@image:%s" % img_a) in result, repr(result))

        # internal events are not our business
        check("%s: internal event declined" % label,
              builder(note_0232(str(img_a), "d", "cap"),
                      make_event("cap", [], internal=True)) is None)

        # hostile event never raises
        class Boom:
            @property
            def text(self):
                raise RuntimeError("no text")

        try:
            builder(note_0232(str(img_a), "d", "cap"), Boom())
            ok = True
        except Exception:
            ok = False
        check("%s: hostile event never raises" % label, ok)

    # read path (shim implementation)
    read = shim.image_note_display_text
    note = note_0231(str(img_a), "prose here", "caption here")
    shown = read(note)
    check("read: projects stored note to caption + refs",
          shown is not None and ("@image:%s" % img_a) in shown and "prose here" not in shown, repr(shown))
    check("read: plain text untouched", read("plain") is None)
    check("read: missing file declines",
          read(note_0231("C:\\nope\\missing.png", "d", "c")) is None)
    check("read: non-string untouched", read(None) is None)


# ---------------------------------------------------------------------------
# 2) patcher tests
# ---------------------------------------------------------------------------

def run_patcher_tests(tmpdir: Path):
    print()
    print("--- patcher (real 0.23.1 / 0.23.2 sources) ---")

    for ver in ("0.23.1", "0.23.2"):
        source = (FIXTURES / ("run_turn_prepare_v%s.py" % ver)).read_text(encoding="utf-8")
        plan = patcher.plan_prepare(source)
        check("prepare %s: exact anchor, ready" % ver,
              plan.status == "patched" and plan.matched_anchor == "exact", plan.as_dict())
        check("prepare %s: patched text compiles" % ver,
              patcher._compile_text_ok(plan.new_text))
        again = patcher.plan_prepare(plan.new_text)
        check("prepare %s: idempotent" % ver, again.status == "already")

        # apply + restore roundtrip on disk
        work = tmpdir / ("prepare_%s" % ver.replace(".", ""))
        (work / "gateway").mkdir(parents=True)
        target = work / "gateway" / "run_turn_prepare.py"
        target.write_text(source, encoding="utf-8")
        plans = {patcher.TARGET_PREPARE: plan}
        backup = work / "backup"
        applied = patcher.apply_plans(work, plans, backup)
        check("prepare %s: applied on disk" % ver,
              applied.get(patcher.TARGET_PREPARE, {}).get("status") == "patched")
        on_disk = patcher.plan_prepare(target.read_text(encoding="utf-8"))
        check("prepare %s: on-disk result is 'already'" % ver, on_disk.status == "already")
        restored = patcher.restore_backup(work, backup)
        check("prepare %s: backup restore roundtrip" % ver,
              restored and target.read_text(encoding="utf-8") == source)

        # CRLF variant
        crlf_work = tmpdir / ("prepare_crlf_%s" % ver.replace(".", ""))
        crlf_work.mkdir()
        plan_crlf = patcher.plan_prepare(source.replace("\n", "\r\n"))
        check("prepare %s: CRLF tree patches" % ver,
              plan_crlf.status == "patched" and "\r\n" in (plan_crlf.new_text or ""))

        # whitespace-tolerant fallback
        drifted = source.replace(
            "        message_text, persist_user_message, persist_user_timestamp = (\n"
            "            self._hmwa_apply_message_timestamp(event, message_text)\n"
            "        )\n",
            "        message_text, persist_user_message, persist_user_timestamp =  (\n"
            "            self._hmwa_apply_message_timestamp( event, message_text )\n"
            "        )\n",
        )
        plan_drift = patcher.plan_prepare(drifted)
        check("prepare %s: whitespace drift -> fallback anchor" % ver,
              plan_drift.status == "patched" and plan_drift.matched_anchor == "whitespace-tolerant",
              plan_drift.as_dict())

        # legacy block replacement (upgrade from an earlier private build)
        v8_tree = source.replace(
            patcher.ANCHOR_A_EXACT,
            patcher.ANCHOR_A_EXACT + patcher.LEGACY_V8_BLOCK_EXACT + "\n",
        )
        plan_v8 = patcher.plan_prepare(v8_tree)
        check("prepare %s: legacy block replaced on upgrade" % ver,
              plan_v8.status == "patched" and plan_v8.removed_legacy == 1
              and "persistence v8" not in (plan_v8.new_text or ""), plan_v8.as_dict())

        # previous private build (v9) -> byte-identical to a fresh patch
        v9_tree = source.replace(patcher.ANCHOR_A_EXACT,
                                 patcher.ANCHOR_A_EXACT + LEGACY_V9_BLOCK_A, 1)
        plan_v9 = patcher.plan_prepare(v9_tree)
        check("prepare %s: earlier build's block replaced byte-identically" % ver,
              plan_v9.status == "patched" and plan_v9.removed_legacy == 1
              and plan_v9.new_text == patcher.plan_prepare(source).new_text, plan_v9.as_dict())

        # refusal on unknown layout
        plan_garbage = patcher.plan_prepare("def unrelated():\n    pass\n")
        check("prepare %s: unknown layout refused cleanly" % ver,
              plan_garbage.status == "refused")

    for ver in ("0.23.1", "0.23.2"):
        source = (FIXTURES / ("run_turn_v%s.py" % ver)).read_text(encoding="utf-8")
        plan = patcher.plan_runner(source)
        check("runner %s: exact anchor, ready" % ver,
              plan.status == "patched" and plan.matched_anchor == "exact", plan.as_dict())
        check("runner %s: patched text compiles" % ver, patcher._compile_text_ok(plan.new_text))
        again = patcher.plan_runner(plan.new_text)
        check("runner %s: idempotent" % ver, again.status == "already")

        # previous private build (v9) -> byte-identical to a fresh patch
        v9_runner = source.replace(patcher.ANCHOR_B_EXACT,
                                   patcher.ANCHOR_B_EXACT + LEGACY_V9_BLOCK_B, 1)
        plan_v9r = patcher.plan_runner(v9_runner)
        check("runner %s: earlier build's block replaced byte-identically" % ver,
              plan_v9r.status == "patched" and plan_v9r.removed_legacy == 1
              and plan_v9r.new_text == patcher.plan_runner(source).new_text, plan_v9r.as_dict())

    # helper install
    work = tmpdir / "helper"
    (work / "gateway").mkdir(parents=True)
    check("helper: installs and is idempotent",
          patcher.install_helper(work) and patcher.install_helper(work)
          and patcher.helper_present(work))


# ---------------------------------------------------------------------------
# 3) shim tests (subprocess: python -I + site.addsitedir, like bootstrap)
# ---------------------------------------------------------------------------

SYNTHETIC_PREPARE = '''
"""Synthetic gateway.run_turn_prepare with the real seam shape (0.23.x)."""


class GatewayTurnPrepareMixin:
    def _hmwa_apply_message_timestamp(self, event, message_text):
        from gateway.run_inbound import strip_discord_triggering_note

        persist_user_message = None
        persist_user_timestamp = None
        if message_text and isinstance(message_text, str):
            persist_user_message = strip_discord_triggering_note(event, message_text)
            message_text = message_text
            persist_user_timestamp = 123.0
        return message_text, persist_user_message, persist_user_timestamp
'''

SYNTHETIC_INBOUND = '''
"""Synthetic gateway.run_inbound with the real strip seam (0.23.x)."""


def strip_discord_triggering_note(event, message_text):
    if not getattr(event, "message_id", None) or not isinstance(message_text, str):
        return message_text
    return message_text
'''

SYNTHETIC_SESSIONS = '''
"""Synthetic hermes_cli.web_routers.sessions with the real projection shape."""


def _project_for_display(messages, *, home=None, inline_images=True):
    return list(messages)
'''

SHIM_TEST_CODE = r'''
import json, os, sys

results = {}

# Mirror hermes_bootstrap.activate_dependencies: the agent root is on sys.path,
# then the dependency environment's site-packages is added via site.addsitedir
# (which executes our .pth and installs the import hook).
sys.path.insert(0, sys.argv[1])
import site
site.addsitedir(sys.argv[2])

import gateway.run_turn_prepare as prepare
import gateway.run_inbound as inbound
import hermes_cli.web_routers.sessions as sessions

img = os.environ["HIP_TEST_IMAGE"]
note = os.environ["HIP_TEST_NOTE"]

class E:
    text = "caption for the turn"
    media_urls = []
    internal = False
    message_id = "m1"

# 1) write seam (prepare)
seam = prepare.GatewayTurnPrepareMixin._hmwa_apply_message_timestamp
results["prepare_wrapped"] = getattr(seam, "_hip_patched", False)
mt, persist, ts = seam(type("S", (prepare.GatewayTurnPrepareMixin,), {})(), E(), note)
results["prepare_durable"] = persist is not None and "@image:" in persist and "Here" not in persist

# 2) write seam (inbound strip)
strip = inbound.strip_discord_triggering_note
results["strip_wrapped"] = getattr(strip, "_hip_patched", False)
out = strip(E(), note)
results["strip_durable"] = "@image:" in out and "Here" not in out

# 3) read seam (REST projection)
proj = sessions._project_for_display
results["project_wrapped"] = getattr(proj, "_hip_patched", False)
rows = proj([{"role": "user", "content": note},
             {"role": "assistant", "content": "ok"},
             {"role": "user", "content": note, "display_kind": "hidden"}])
results["project_display"] = (
    "@image:" in (rows[0].get("display_content") or "")
    and "display_content" not in rows[1]
    and "display_content" not in rows[2])

# 4) idempotency: re-run addsitedir (bootstrap does it again on relaunch)
import site
site.addsitedir(sys.argv[2])
results["single_wrap"] = getattr(prepare.GatewayTurnPrepareMixin._hmwa_apply_message_timestamp, "_hip_patched", False)

print(json.dumps(results))
'''


def run_shim_tests(tmpdir: Path):
    print()
    print("--- shim (python -I + site.addsitedir, like hermes_bootstrap) ---")

    agent_root = tmpdir / "agent"
    (agent_root / "gateway").mkdir(parents=True)
    (agent_root / "gateway" / "__init__.py").write_text("", encoding="utf-8")
    (agent_root / "gateway" / "run_turn_prepare.py").write_text(SYNTHETIC_PREPARE, encoding="utf-8")
    (agent_root / "gateway" / "run_inbound.py").write_text(SYNTHETIC_INBOUND, encoding="utf-8")
    (agent_root / "hermes_cli").mkdir(parents=True)
    (agent_root / "hermes_cli" / "__init__.py").write_text("", encoding="utf-8")
    (agent_root / "hermes_cli" / "web_routers").mkdir()
    (agent_root / "hermes_cli" / "web_routers" / "__init__.py").write_text("", encoding="utf-8")
    (agent_root / "hermes_cli" / "web_routers" / "sessions.py").write_text(SYNTHETIC_SESSIONS, encoding="utf-8")

    venv = tmpdir / "venv"
    # Write pyvenv.cfg BEFORE computing site_packages() so the version read
    # from the cfg is used consistently (otherwise site_packages() falls back
    # to the running interpreter's version and the shim lands in a different
    # lib/pythonX.Y/site-packages subdir than the one we pass to the subprocess).
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text("home = /x\nversion = 3.12.0\n", encoding="utf-8")
    sp = discovery.site_packages(venv)
    sp.mkdir(parents=True)

    ok, detail = shim_install.install_shim(venv)
    check("shim: planted into simulated venv", ok, detail)
    check("shim: .pth + module present", shim_install.shim_installed(venv))

    img = tmpdir / "pic abc.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")
    note = note_0232(str(img), "prose about the image", "caption for the turn")

    env = dict(os.environ)
    env["HIP_TEST_IMAGE"] = str(img)
    env["HIP_TEST_NOTE"] = note
    env["PYTHONUTF8"] = "1"  # force UTF-8 in the subprocess on Windows

    code = SHIM_TEST_CODE
    proc = subprocess.run(
        [sys.executable, "-I", "-c", code, str(agent_root), str(sp).replace("\\", "/")],
        capture_output=True, text=True, env=env, cwd=str(tmpdir), timeout=60,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        check("shim: subprocess ran", False, proc.stderr[-600:])
        return
    try:
        results = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:
        check("shim: parsed results", False, "%r / %s" % (exc, proc.stdout[-400:]))
        return

    check("shim: import hook active under -I", results.get("prepare_wrapped") is True, results)
    check("shim: prepare seam returns durable text", results.get("prepare_durable") is True, results)
    check("shim: strip seam wrapped + durable", results.get("strip_wrapped") is True
          and results.get("strip_durable") is True, results)
    check("shim: REST projection wrapped + display_content set",
          results.get("project_wrapped") is True and results.get("project_display") is True, results)
    check("shim: double addsitedir stays single-wrapped", results.get("single_wrap") is True, results)

    # disabled via env
    env_off = dict(env)
    env_off["HERMES_IMAGE_PERSIST"] = "off"
    proc_off = subprocess.run(
        [sys.executable, "-I", "-c",
         "import sys; sys.path.insert(0, %r); import site; site.addsitedir(%r); "
         "import gateway.run_turn_prepare as p; "
         "print(getattr(p.GatewayTurnPrepareMixin._hmwa_apply_message_timestamp, '_hip_patched', False))"
         % (str(agent_root), str(sp).replace("\\", "/"))],
        capture_output=True, text=True, env=env_off, cwd=str(tmpdir), timeout=60,
        encoding="utf-8", errors="replace",
    )
    check("shim: HERMES_IMAGE_PERSIST=off leaves seams untouched",
          proc_off.returncode == 0 and proc_off.stdout.strip() == "False", proc_off.stderr[-300:])


# ---------------------------------------------------------------------------
# 3b) integration: the REAL 0.23.2 run_turn_prepare.py through the shim
# ---------------------------------------------------------------------------

REAL_MODULE_TEST_CODE = r'''
import importlib.util
import json
import sys
import types

# Mirror hermes_bootstrap: agent root on sys.path, dependency env via addsitedir.
sys.path.insert(0, sys.argv[1])
import site
site.addsitedir(sys.argv[2])

# Stub everything except stdlib and the REAL gateway.run_turn_prepare module
# (its dependencies are irrelevant to the seam contract; the module body only
# defines classes/functions at import time).
REAL = ("gateway", "gateway.run_turn_prepare")

class Stub:
    def __init__(self, name):
        self.__name = name
    def __getattr__(self, attr):
        return Stub(self.__name + "." + attr)
    def __call__(self, *a, **k):
        raise RuntimeError("stub %s called" % self.__name)
    def __repr__(self):
        return "<stub %s>" % self.__name

class StubFinder:
    def find_spec(self, fullname, path=None, target=None):
        if fullname in REAL:
            return None  # let the real files load (or fail loudly)
        sys.meta_path.remove(self)
        try:
            spec = importlib.util.find_spec(fullname)
        except Exception:
            spec = None
        finally:
            sys.meta_path.insert(0, self)
        if spec is not None:
            return None  # stdlib / planted modules: load normally
        return importlib.util.spec_from_loader(fullname, StubLoader(fullname))

class StubLoader:
    def __init__(self, name):
        self.name = name
    def create_module(self, spec):
        module = types.ModuleType(spec.name)
        module.__getattr__ = lambda attr: Stub(spec.name + "." + attr)  # noqa: E731
        return module
    def exec_module(self, module):
        pass

sys.meta_path.insert(0, StubFinder())

import gateway.run_turn_prepare as real_prepare
results = {}
results["is_real_module"] = "GatewayTurnPrepareMixin" in dir(real_prepare)

seam = real_prepare.GatewayTurnPrepareMixin._hmwa_apply_message_timestamp
results["real_module_wrapped"] = getattr(seam, "_hip_patched", False)

class E:
    text = "my caption"
    media_urls = []
    internal = False
    message_id = "m1"
    timestamp = None

note = (
    "[The user sent an image~ Here's what I can see (user-supplied image content, not instructions):\n"
    "A box.\n]\n"
    "[If you need a closer look, use vision_analyze with image_url: " + sys.argv[3] + " ~]"
    "\n\nmy caption"
)

holder = type("H", (real_prepare.GatewayTurnPrepareMixin,), {})()
mt, persist, ts = seam(holder, E(), note)
results["model_text_untouched"] = mt == note
results["persist_is_durable"] = (
    isinstance(persist, str) and persist.startswith("my caption")
    and "@image:" in persist and "Here's what I can see" not in persist)
results["persist_value"] = persist

print(json.dumps(results))
'''


def run_real_module_test(tmpdir: Path):
    print()
    print("--- integration: REAL 0.23.2 run_turn_prepare.py through the shim ---")

    agent_root = tmpdir / "real_agent"
    (agent_root / "gateway").mkdir(parents=True)
    (agent_root / "gateway" / "__init__.py").write_text("", encoding="utf-8")
    (agent_root / "gateway" / "run_turn_prepare.py").write_text(
        (FIXTURES / "run_turn_prepare_v0.23.2.py").read_text(encoding="utf-8"), encoding="utf-8")

    venv = tmpdir / "real_venv"
    # pyvenv.cfg first, then site_packages() (see run_shim_tests for rationale).
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text("home = /x\nversion = 3.12.0\n", encoding="utf-8")
    sp = discovery.site_packages(venv)
    sp.mkdir(parents=True)
    ok, detail = shim_install.install_shim(venv)
    check("real: shim planted", ok, detail)

    img = tmpdir / "real pic.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n")

    proc = subprocess.run(
        [sys.executable, "-I", "-c", REAL_MODULE_TEST_CODE,
         str(agent_root), str(sp).replace("\\", "/"), str(img)],
        capture_output=True, text=True, timeout=60,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        check("real: subprocess ran", False, proc.stderr[-700:])
        return
    try:
        results = json.loads(proc.stdout.strip().splitlines()[-1])
    except Exception as exc:
        check("real: parsed results", False, "%r / %s" % (exc, proc.stdout[-400:]))
        return
    check("real: 0.23.2 module imported with stubs", results.get("is_real_module") is True, results)
    check("real: shim wrapped the real class", results.get("real_module_wrapped") is True, results)
    check("real: model text untouched", results.get("model_text_untouched") is True, results)
    check("real: persist swapped to caption + @image:", results.get("persist_is_durable") is True,
          results.get("persist_value"))


# ---------------------------------------------------------------------------
# 4) guard tests
# ---------------------------------------------------------------------------

def run_guard_tests(tmpdir: Path):
    print()
    print("--- guard (simulated install + update) ---")

    home = tmpdir / "home"
    root = home / "hermes-agent"
    (root / "gateway").mkdir(parents=True)
    (root / "gateway" / "run_turn_prepare.py").write_text(
        (FIXTURES / "run_turn_prepare_v0.23.2.py").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "gateway" / "run_turn.py").write_text(
        (FIXTURES / "run_turn_v0.23.2.py").read_text(encoding="utf-8"), encoding="utf-8")

    venv = home / "installs" / "abc123" / "environments" / "gen-1"
    # pyvenv.cfg first, then site_packages() (see run_shim_tests for rationale).
    venv.mkdir(parents=True, exist_ok=True)
    (venv / "pyvenv.cfg").write_text("home = /x\nversion = 3.12.0\n", encoding="utf-8")
    sp = discovery.site_packages(venv)
    sp.mkdir(parents=True)
    facts_dir = home / "installs" / discovery.install_key(root)
    facts_dir.mkdir(parents=True, exist_ok=True)
    (facts_dir / "facts.json").write_text(
        json_dumps({"packages": {"venv": {"environment": str(venv)}}}), encoding="utf-8")

    state = {}
    report = guard_core.guard_pass(home, state)
    check("guard: first pass ok", report["status"] == "ok", report)
    check("guard: shim planted into committed env",
          any(e["ok"] and e["kind"] == "committed" for e in report["shim"]), report["shim"])
    check("guard: source patches applied",
          patcher.helper_present(root)
          and patcher.plan_prepare((root / "gateway" / "run_turn_prepare.py").read_text(encoding="utf-8")).status == "already",
          report["source"])

    state2 = state_mod.load_state(home)
    check("guard: state.json persisted", state2.get("last_status") == "ok")

    # second pass: nothing changed -> still ok, no new backup, no patch work
    report2 = guard_core.guard_pass(home, state2)
    check("guard: second pass is a no-op", report2["status"] == "ok"
          and not report2.get("notes"), report2.get("notes"))

    # simulate an update: tree replaced (fresh upstream file), new generation venv
    (root / "gateway" / "run_turn_prepare.py").write_text(
        (FIXTURES / "run_turn_prepare_v0.23.2.py").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "gateway" / "run_turn.py").write_text(
        (FIXTURES / "run_turn_v0.23.2.py").read_text(encoding="utf-8"), encoding="utf-8")
    gen2 = home / "installs" / discovery.install_key(root) / "environments" / "gen-2"
    # pyvenv.cfg first, then site_packages().
    gen2.mkdir(parents=True, exist_ok=True)
    (gen2 / "pyvenv.cfg").write_text("home = /x\nversion = 3.12.0\n", encoding="utf-8")
    sp2 = discovery.site_packages(gen2)
    sp2.mkdir(parents=True)
    (facts_dir / "facts.json").write_text(
        json_dumps({"packages": {"venv": {"environment": str(gen2)}}}), encoding="utf-8")

    state3 = state_mod.load_state(home)
    report3 = guard_core.guard_pass(home, state3)
    # update is detected via plan_all() (fresh upstream files need patching),
    # no fingerprint/hash involved - the guard re-applies the patches and the
    # post-patch tree reads as "already".
    check("guard: update detected and re-patched",
          report3["status"] == "ok"
          and any("source patches applied" in n for n in report3.get("notes", [])),
          report3.get("notes"))
    check("guard: re-applied source patches after update",
          patcher.plan_prepare((root / "gateway" / "run_turn_prepare.py").read_text(encoding="utf-8")).status == "already")
    planted = {e["venv"] for e in report3["shim"] if e["ok"]}
    check("guard: shim re-planted into the new generation", str(gen2.resolve()) in planted,
          report3["shim"])

    # removal
    removed = shim_install.remove_shim_everywhere(home, root)
    check("shim: removal cleans every environment", removed and not any(
        shim_install.shim_installed(Path(v)) for v, _ in discovery.discover_venvs(home, root)))


def json_dumps(data):
    import json

    return json.dumps(data)


# ---------------------------------------------------------------------------

def main() -> int:
    print("Hermes Image Persist 1.0.0 selfcheck")
    print("python : %s (%s)" % (sys.version.split()[0], sys.executable))
    with tempfile.TemporaryDirectory(prefix="hip_check_") as raw:
        tmpdir = Path(raw)
        run_payload_tests(tmpdir)
        run_patcher_tests(tmpdir)
        run_shim_tests(tmpdir)
        run_real_module_test(tmpdir)
        run_guard_tests(tmpdir)
    print()
    print("passed=%d failed=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
