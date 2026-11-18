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

        # v1.0.1: file:// URL in the note is unwrapped to a real path
        file_url_note = (
            "[The user sent an image~ Here's what I can see:\nprose\n"
            "image_url: file:///%s ~]\n\ncap" % str(img_a).replace("\\", "/")
        )
        result = builder(file_url_note, make_event("cap", []))
        check("%s: file:// URL unwrapped to a path" % label,
              result is not None and ("@image:%s" % img_a) in result, repr(result))

        # v1.0.1: bare file name in the note resolves next to the event's path
        sub = tmpdir / "cachedir"
        sub.mkdir(exist_ok=True)
        cached = sub / "upload.png"
        cached.write_bytes(b"\x89PNG\r\n\x1a\n")
        bare_note = (
            "[The user sent an image~ Here's what I can see:\nprose\n"
            "image_url: upload.png ~]\n\ncap"
        )
        result = builder(bare_note, make_event("cap", [str(cached)]))
        check("%s: bare name in note resolves via event path" % label,
              result is not None and ("@image:%s" % cached) in result, repr(result))

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

SYNTHETIC_RUN_TURN = '''
"""Synthetic gateway.run_turn binding the strip seam at MODULE level."""

from gateway.run_inbound import strip_discord_triggering_note  # noqa: F401

STRIP_IN_RUN_TURN = strip_discord_triggering_note
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
import gateway.run_turn as run_turn
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

# 2b) v1.0.1: the seam name hoisted into run_turn at import time is wrapped too
hoisted = getattr(run_turn, "STRIP_IN_RUN_TURN", None)
results["runturn_hoisted_wrapped"] = getattr(hoisted, "_hip_patched", False)
results["runturn_module_wrapped"] = getattr(
    getattr(run_turn, "strip_discord_triggering_note", None), "_hip_patched", False)

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
    # v1.0.1: a run_turn module that from-imports the strip seam at MODULE level
    # (a build where the hoisted import binds the name before either hook ran).
    (agent_root / "gateway" / "run_turn.py").write_text(SYNTHETIC_RUN_TURN, encoding="utf-8")
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
    check("shim: run_turn hoisted + module seams wrapped (v1.0.1)",
          results.get("runturn_hoisted_wrapped") is True
          and results.get("runturn_module_wrapped") is True, results)
    check("shim: REST projection wrapped + display_content set",
          results.get("project_wrapped") is True and results.get("project_display") is True, results)
    check("shim: double addsitedir stays single-wrapped", results.get("single_wrap") is True, results)

    # v1.0.1: verify_shim() proves the .pth executes from the env's own python.
    # The simulated venv has no real interpreter, so verification must report
    # "no python" rather than claim success - the honest-negative path.
    verified, vdetail = shim_install.verify_shim(venv)
    check("shim: verify_shim honest when env has no python",
          verified is False and "no python" in vdetail, (verified, vdetail))

    # A REAL venv (this interpreter): plant, verify, remove.
    real_venv = tmpdir / "real_env"
    try:
        import venv as _venv

        _venv.create(str(real_venv), with_pip=False)
    except Exception as exc:
        check("shim: real-venv creation (skipped)", False, repr(exc))
        real_venv = None
    if real_venv is not None:
        ok, detail = shim_install.install_shim(real_venv, verify=True)
        check("shim: real venv planted", ok and "UNVERIFIED" not in detail, detail)
        verified, vdetail = shim_install.verify_shim(real_venv)
        check("shim: real venv verified via its own python -I", verified, vdetail)
        # and the .pth line really executes in that env under -I
        proc = subprocess.run(
            [str(discovery.venv_python(real_venv)), "-I", "-c",
             "import sys; print(getattr(sys, '_hermes_image_persist_installed', False))"],
            capture_output=True, text=True, timeout=60, encoding="utf-8", errors="replace")
        check("shim: .pth executes under -I in real venv",
              proc.returncode == 0 and proc.stdout.strip() == "True", proc.stderr[-200:])
        shim_install.remove_shim(real_venv)

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
    # v1.0.1: give the simulated env a REAL interpreter so shim verification
    # passes and a fully-clean pass stays a no-op (no UNVERIFIED notes).
    try:
        import venv as _venv

        real_bin = venv / "Scripts" if os.name == "nt" else venv / "bin"
        py_name = "python.exe" if os.name == "nt" else "python3"
        if not (real_bin / py_name).is_file():
            import shutil as _shutil

            real_bin.mkdir(parents=True, exist_ok=True)
            _shutil.copy2(sys.executable, real_bin / py_name)
            # a copied interpreter needs its stdlib: point it at ours via a cfg
            base = Path(sys.base_prefix if hasattr(sys, "base_prefix") else sys.prefix)
            (venv / "pyvenv.cfg").write_text(
                "home = %s\nversion = %d.%d.%d\ninclude-system-site-packages = true\n"
                % (base, sys.version_info[0], sys.version_info[1], sys.version_info[2]),
                encoding="utf-8")
    except Exception:
        pass
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

    # v1.0.1 regression guard: a LIVE Hermes process must NOT defer the source
    # patch any more. 1.0.0 postponed it forever with an always-running
    # Desktop; now the patch lands immediately (atomic, compile-checked) and
    # only the running process keeps its in-memory modules until restart.
    (root / "gateway" / "run_turn_prepare.py").write_text(
        (FIXTURES / "run_turn_prepare_v0.23.2.py").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "gateway" / "run_turn.py").write_text(
        (FIXTURES / "run_turn_v0.23.2.py").read_text(encoding="utf-8"), encoding="utf-8")

    live_marker = tmpdir / "live_hermes_marker"
    live_marker.write_text("x", encoding="utf-8")

    # Mock ONLY the two functions the guard consults; keep the rest of the real
    # discovery module (version_string etc.) intact.
    real_running = guard_core.discovery.hermes_process_running
    real_discover = guard_core.discovery.discover_venvs
    guard_core.discovery.hermes_process_running = staticmethod(
        lambda root: (True, "fake live process"))
    guard_core.discovery.discover_venvs = staticmethod(
        lambda home, root, include_running=True: [])
    try:
        report_live = guard_core.guard_pass(home, state)
    finally:
        guard_core.discovery.hermes_process_running = real_running
        guard_core.discovery.discover_venvs = real_discover
    check("guard: source patch applied even with Hermes running",
          patcher.plan_prepare((root / "gateway" / "run_turn_prepare.py").read_text(encoding="utf-8")).status == "already"
          and report_live["status"] != "ok_no_source",
          (report_live.get("status"), report_live.get("source")))
    check("guard: no 'deferred' note when Hermes runs",
          not any("deferred" in n for n in report_live.get("notes", [])),
          report_live.get("notes"))

    # restore patched state for the idempotency pass below
    state2 = state_mod.load_state(home)
    check("guard: state.json persisted", state2.get("last_status") == "ok")

    # second pass: nothing changed -> still ok, no new backup, no patch work
    report2 = guard_core.guard_pass(home, state2)
    check("guard: second pass is a no-op", report2["status"] == "ok"
          and not report2.get("notes"), report2.get("notes"))

    # v1.0.2: a gateway process that started BEFORE the fix must flip the pass
    # to needs_gateway_restart. v1.0.3 uses the process-dict API
    # (live_gateway_processes), so monkeypatch THAT.
    fake_gw = {"pid": 4242, "ppid": 100, "name": "python.exe",
               "cmdline": r"C:\hermes\venv\Scripts\python.exe -m gateway.run serve",
               "start_epoch": 1000000000}                  # started long ago
    real_live_gw = guard_core.discovery.live_gateway_processes
    try:
        guard_core.discovery.live_gateway_processes = staticmethod(lambda: [dict(fake_gw)])
        state2["first_installed_epoch"] = 2000000000       # fix installed later
        report_stale = guard_core.guard_pass(home, state2)

        # and a gateway started AFTER the fix must NOT trip it
        state3b = state_mod.load_state(home)
        fresh_gw = dict(fake_gw, start_epoch=2500000000)   # started after the fix
        guard_core.discovery.live_gateway_processes = staticmethod(lambda: [fresh_gw])
        report_fresh = guard_core.guard_pass(home, state3b)
    finally:
        guard_core.discovery.live_gateway_processes = real_live_gw
    check("guard: pre-fix gateway -> needs_gateway_restart",
          report_stale["status"] == "needs_gateway_restart"
          and any("started BEFORE the fix" in n for n in report_stale.get("notes", [])),
          (report_stale.get("status"), report_stale.get("notes")))
    check("guard: gateway restart hint present in note",
          any("restart" in n.lower() for n in report_stale.get("notes", [])),
          report_stale.get("notes"))
    check("guard: gateway pid listed in the note",
          any("4242" in n for n in report_stale.get("notes", [])),
          report_stale.get("notes"))
    check("guard: post-fix gateway -> no restart demand",
          report_fresh["status"] != "needs_gateway_restart",
          (report_fresh.get("status"), report_fresh.get("notes")))

    # restore clean state for the update-simulation below
    state3 = state_mod.load_state(home)

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
# 5) discovery tests - standalone Python under <home>/tools/
# ---------------------------------------------------------------------------

def run_discovery_tests(tmpdir: Path):
    print()
    print("--- discovery (standalone Python under tools/) ---")

    # Use a separate subdirectory so we don't collide with run_guard_tests.
    home = tmpdir / "disc_home"
    root = home / "hermes-agent"
    root.mkdir(parents=True, exist_ok=True)

    # Hermes ships a standalone interpreter under tools/ (no pyvenv.cfg).
    tools_py = home / "tools" / "python-3.14.9+20261021-win32-x64"
    tools_py.mkdir(parents=True)
    if os.name == "nt":
        (tools_py / "python.exe").write_bytes(b"")
    else:
        (tools_py / "bin").mkdir()
        (tools_py / "bin" / "python3").write_bytes(b"")

    venvs = discovery.discover_venvs(home, root)
    kinds = {k for _, k in venvs}
    check("discovery: standalone Python under tools/ found",
          any(k == "standalone" for _, k in venvs), venvs)

    # site_packages() must return a usable path for the standalone root.
    sp = discovery.site_packages(tools_py)
    check("discovery: site_packages() resolves for standalone Python",
          "site-packages" in str(sp).lower(), sp)

    # facts.json pointing to a standalone Python must be accepted as committed.
    facts_dir = home / "installs" / discovery.install_key(root)
    facts_dir.mkdir(parents=True, exist_ok=True)
    (facts_dir / "facts.json").write_text(
        json_dumps({"packages": {"venv": {"environment": str(tools_py)}}}), encoding="utf-8")
    committed = discovery.committed_environment(home, root)
    check("discovery: committed_environment accepts standalone Python",
          committed is not None and Path(committed).resolve() == tools_py.resolve(),
          committed)

    # v1.0.1: facts.json under a SIBLING installs/* dir (PM re-keyed its state
    # dir between versions) is still found as the committed environment.
    sibling_dir = home / "installs" / "zz-otherkey"
    sibling_dir.mkdir(parents=True, exist_ok=True)
    other_venv = home / "installs" / "zz-otherkey" / "environments" / "gen-9"
    other_venv.mkdir(parents=True, exist_ok=True)
    (other_venv / "pyvenv.cfg").write_text("home = /x\nversion = 3.12.0\n", encoding="utf-8")
    (sibling_dir / "facts.json").write_text(
        json_dumps({"packages": {"venv": {"environment": str(other_venv)}}}), encoding="utf-8")
    # remove the primary facts.json so ONLY the sibling can satisfy the lookup
    (facts_dir / "facts.json").unlink()
    committed_sibling = discovery.committed_environment(home, root)
    check("discovery: sibling installs/*/facts.json found (PM re-key)",
          committed_sibling is not None and Path(committed_sibling).resolve() == other_venv.resolve(),
          committed_sibling)
    venvs_after = discovery.discover_venvs(home, root)
    check("discovery: sibling generation venvs discovered",
          any(Path(v).resolve() == other_venv.resolve() for v, _ in venvs_after), venvs_after)

    # v1.0.1: interpreter_env_from_command maps a live process command line
    # to its environment root.
    cmd_win = r"C:\Users\u\AppData\Local\hermes\installs\k1\environments\gen-2\Scripts\python.exe -I -m gateway.run"
    mapped = discovery.interpreter_env_from_command(cmd_win)
    check("discovery: live command line -> env root (Windows shape)",
          mapped is not None and mapped.as_posix().endswith("gen-2"), mapped)
    cmd_nix = "/home/u/.hermes/installs/k1/environments/gen-2/bin/python3 -m gateway.run"
    mapped2 = discovery.interpreter_env_from_command(cmd_nix)
    check("discovery: live command line -> env root (POSIX shape)",
          mapped2 is not None and mapped2.as_posix().endswith("gen-2"), mapped2)
    check("discovery: non-python command line -> None",
          discovery.interpreter_env_from_command("C:\\Windows\\notepad.exe file.txt") is None)

    # v1.0.3: gateway classification from a PROCESS TABLE. This is the exact
    # field failure of v1.0.2: the PowerShell probe matched ITSELF ('serve'/
    # 'gateway.run' inside the query string), so installers saw powershell.exe
    # as "gateways" and never touched the real one. Now classification runs in
    # Python over one snapshot; probe shells carry pid=-1 and are excluded.
    real_snapshot = discovery._snapshot_processes
    fake_table = [
        # pid,   ppid,  cmdline,                                                     name,           start
        (-1,     111,   "powershell.exe ... Get-CimInstance ... gateway.run ...",    "powershell.exe", None),  # v1.0.2 self-match!
        (101,    100,   r"C:\hermes\apps\desktop\win-unpacked\Hermes.exe",           "Hermes.exe",    3000000000),  # Desktop shell
        (102,    101,   r"C:\hermes\Hermes.exe --type=renderer --user-data-dir=...", "Hermes.exe",    3000000001),  # Chromium child
        (200,    100,   r"C:\venv\Scripts\python.exe -I -m gateway.run",             "python.exe",    3000000002),  # REAL gateway
        (201,    200,   r"C:\hermes\llama-server.exe --model g",                     "llama-server.exe", 3000000003),  # its child
        (300,    299,   r"C:\Windows\System32\someapp.exe",                          "someapp.exe",   None),         # noise
    ]
    try:
        discovery._snapshot_processes = lambda: [list(r) for r in fake_table]
        procs = discovery.live_gateway_processes()
        cmds = discovery.live_gateway_commands()
    finally:
        discovery._snapshot_processes = real_snapshot
    gw_pids = [g["pid"] for g in procs]
    check("discovery: probe shell (v1.0.2 self-match) never classified as gateway",
          all(p not in gw_pids for p in (-1,)), gw_pids)
    check("discovery: Desktop shell + Chromium children excluded",
          101 not in gw_pids and 102 not in gw_pids, gw_pids)
    check("discovery: real gateway (python -m gateway.run) detected",
          200 in gw_pids, gw_pids)
    check("discovery: llama-server itself NOT a target (its parent is)",
          201 not in gw_pids, gw_pids)
    check("discovery: gateway carry start_epoch from the table",
          all(g.get("start_epoch") for g in procs if g["pid"] == 200), procs)

    # llama-server parent chain: opaque-parent gateway (hermes.exe with no
    # readable args) is still found THROUGH its supervised llama-server child.
    fake_table2 = [
        (101,    100,   r"C:\hermes\apps\desktop\win-unpacked\Hermes.exe",  "Hermes.exe",        3000000000),
        (900,    899,   r"C:\hermes\hermes.exe",                           "hermes.exe",        3000000004),  # opaque
        (901,    900,   r"C:\hermes\llama-server.exe --port 18434",        "llama-server.exe",  3000000005),
    ]
    try:
        discovery._snapshot_processes = lambda: [list(r) for r in fake_table2]
        procs2 = discovery.live_gateway_processes()
    finally:
        discovery._snapshot_processes = real_snapshot
    check("discovery: opaque gateway found via llama-server parent chain",
          any(g["pid"] == 900 and g.get("via") for g in procs2), procs2)
    check("discovery: Desktop-only Hermes.exe still not flagged (no llama child)",
          all(g["pid"] != 101 for g in procs2), procs2)

    # v1.0.3: _parse_cim_date handles both WMI spellings
    check("discovery: _parse_cim_date CIM DateTime",
          discovery._parse_cim_date("20261118005843.500000+180") == 1794963523,
          discovery._parse_cim_date("20261118005843.500000+180"))
    check("discovery: _parse_cim_date /Date()/",
          discovery._parse_cim_date("/Date(1794963523000)/") == 1794963523,
          discovery._parse_cim_date("/Date(1794956323000)/"))
    check("discovery: _parse_cim_date garbage -> None",
          discovery._parse_cim_date("") is None
          and discovery._parse_cim_date("not a date") is None)

    # v1.0.3: venv_python finds standalone-layout Pythons (python.exe in the
    # ROOT) - the cause of the permanent "no python in this environment"
    # UNVERIFIED noise for tools/python-* environments in v1.0.2.
    standalone_root = tmpdir / "python-3.14.7-x64"
    standalone_root.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        py = standalone_root / "python.exe"
    else:
        (standalone_root / "bin").mkdir(exist_ok=True)
        py = standalone_root / "bin" / "python3"
    py.write_text("#!stub\n", encoding="utf-8")
    check("discovery: venv_python resolves standalone-layout python",
          discovery.venv_python(standalone_root).is_file()
          and discovery.venv_python(standalone_root) == py,
          discovery.venv_python(standalone_root))

    # v1.0.3: process_start_epochs reads from the TABLE now: every entry has a
    # resolved start epoch for processes that expose a cmdline on POSIX.
    if os.path.exists("/proc/self/stat"):
        epochs = discovery.process_start_epochs([])
        check("discovery: process_start_epochs returns a populated table",
              bool(epochs) and all(v > 0 for v in epochs.values()), len(epochs))
    else:
        check("discovery: process_start_epochs (POSIX only, skipped here)", True)


# ---------------------------------------------------------------------------

def main() -> int:
    print("Hermes Image Persist 1.0.3 selfcheck")
    print("python : %s (%s)" % (sys.version.split()[0], sys.executable))
    with tempfile.TemporaryDirectory(prefix="hip_check_") as raw:
        tmpdir = Path(raw)
        run_payload_tests(tmpdir)
        run_patcher_tests(tmpdir)
        run_shim_tests(tmpdir)
        run_real_module_test(tmpdir)
        run_guard_tests(tmpdir)
        run_discovery_tests(tmpdir)
    print()
    print("passed=%d failed=%d" % (PASS, FAIL))
    return 0 if FAIL == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
