"""Adaptive source-patch engine (layer 2).

Patches two seams in the Hermes agent tree and installs one new helper file:

* ``gateway/run_turn_prepare.py``  - insert the durable-text swap right after
  the ``_hmwa_apply_message_timestamp`` unpack inside ``_hmwa_prepare_turn``
  (anchor A). The exact anchor is byte-identical in v0.23.1 (a84025a) and
  v0.23.2 (ae2a81c); whitespace-tolerant and structural fallbacks cover
  minor refactors.
* ``gateway/run_turn.py``  - the queued follow-up path persists via
  ``strip_discord_triggering_note(pending_event, next_message)`` (anchor B,
  also identical in both versions); the same swap is inserted after it.
* ``gateway/local_image_persist.py``  - new inert helper file.

The engine is deliberately conservative: every patch is written only if the
patched text still compiles (``py_compile``), every original file is backed up
first, blocks left behind by earlier private builds of this fix are replaced,
and unknown layouts cause a clean refusal (status ``refused`` /
``needs_adaptation``) instead of a guess.
"""
from __future__ import annotations

import py_compile
import re
import shutil
import tempfile
from pathlib import Path

from . import payload_source

PATCH_MARKER = "# Hermes Image Persist:"
HELPER_NAME = payload_source.HELPER_NAME

TARGET_PREPARE = "gateway/run_turn_prepare.py"
TARGET_RUNNER = "gateway/run_turn.py"
TARGETS = (TARGET_PREPARE, TARGET_RUNNER)

# ---------------------------------------------------------------------------
# anchors
# ---------------------------------------------------------------------------

ANCHOR_A_EXACT = """\
        message_text, persist_user_message, persist_user_timestamp = (
            self._hmwa_apply_message_timestamp(event, message_text)
        )
"""

ANCHOR_A_FALLBACK = re.compile(
    r"^(?P<indent>[ \t]*)message_text,[ \t]*persist_user_message,[ \t]*persist_user_timestamp[ \t]*=[ \t]*\(\n"
    r"(?P=indent)[ \t]*self\._hmwa_apply_message_timestamp\([ \t]*event,[ \t]*message_text[ \t]*\)[ \t]*\n"
    r"(?P=indent)\)[ \t]*\n",
    re.MULTILINE,
)

ANCHOR_B_EXACT = """\
            next_persist_message = strip_discord_triggering_note(pending_event, next_message)
"""

ANCHOR_B_FALLBACK = re.compile(
    r"^(?P<indent>[ \t]*)next_persist_message[ \t]*=[ \t]*"
    r"strip_discord_triggering_note\([ \t]*pending_event[ \t]*,[ \t]*next_message[ \t]*\)[ \t]*\n",
    re.MULTILINE,
)

# Older private builds of this fix inserted their own marked blocks; the
# exact texts are matched first, loose regexes cover hand-edited copies.
LEGACY_V8_BLOCK_EXACT = """\
        # Hermes LOCAL text-image persistence v8: split model payload from durable UI input.
        # Text-mode pre-analysis embedded model-only prose into message_text, and
        # _hmwa_apply_message_timestamp() copied it into persist_user_message, so the
        # saved user bubble lost its @image: refs and the Desktop swapped the thumbnail
        # for the enrichment note after the reply. Native turns already persist refs;
        # rebuild the durable message from the inbound event itself. The LLM input
        # (message_text) is never modified.
        if str(getattr(source, "platform", "") or "").lower() == "local" and not getattr(event, "internal", False):
            try:
                from gateway.local_image_persist_v8 import durable_local_text_image_message_v8
                _local_image_display_v8 = durable_local_text_image_message_v8(
                    persist_user_message if isinstance(persist_user_message, str) else message_text, event
                )
                if _local_image_display_v8 is not None:
                    persist_user_message = _local_image_display_v8
            except Exception as _v8_exc:
                try:
                    from gateway.local_image_persist_v8 import log_v8
                    log_v8("prepare-stage hook failed: %r" % (_v8_exc,))
                except Exception:
                    pass
"""

LEGACY_V7_REMOVE_RE = re.compile(
    r"[ \t]*# Hermes LOCAL text-image persistence v7:.*?"
    r'logger\.info\("Image persistence v7: durable LOCAL image references set"\)\r?\n?',
    re.DOTALL,
)

# Loose fallbacks for hand-edited legacy blocks. Each stops at the block's own
# final bare ``pass`` line (lazy quantifier), never eating the code after it.
LEGACY_V8_REMOVE_RE = re.compile(
    r"(?m)^[ \t]*# Hermes LOCAL text-image persistence v8:[^\n]*\n"
    r"(?:^[ \t]*#[^\n]*\n)*"
    r"^[ \t]*if str\(getattr\(source, \"platform\", \"\"\) or \"\"\)\.lower\(\) == \"local\""
    r"[^\n]*\n(?:^[ \t]+[^\n]*\n)*?^[ \t]*pass[ \t]*\n"
)

LEGACY_V9_PREPARE_REMOVE_RE = re.compile(
    r"(?m)^[ \t]*# Hermes image persist v9: durable text[^\n]*\n"
    r"(?:^[ \t]*#[^\n]*\n)*"
    r"^[ \t]*if not getattr\(event, \"internal\", False\):[^\n]*\n"
    r"(?:^[ \t]+[^\n]*\n)*?^[ \t]*pass[ \t]*\n"
)

LEGACY_V9_RUNNER_REMOVE_RE = re.compile(
    r"(?m)^[ \t]*# Hermes image persist v9: queued follow-ups[^\n]*\n"
    r"^[ \t]*if not getattr\(pending_event, \"internal\", False\):[^\n]*\n"
    r"(?:^[ \t]+[^\n]*\n)*?^[ \t]*pass[ \t]*\n"
)


# ---------------------------------------------------------------------------
# insertion blocks
# ---------------------------------------------------------------------------

def block_a(indent: str = "        ") -> str:
    pad = indent
    lines = [
        '%s%s durable text for text-mode image turns (upstream #142500).' % (pad, PATCH_MARKER),
        "%s# Text-mode pre-analysis swaps message_text for the vision-enrichment note, and the" % pad,
        "%s# note leaked into persist_user_message, so the Desktop lost the @image: thumbnail." % pad,
        "%s# Rebuild the durable row from the event (caption + @image: refs); message_text" % pad,
        "%s# (what the model sees) is never modified. Platform-agnostic like the upstream fix." % pad,
        '%sif not getattr(event, "internal", False):' % pad,
        "%s    try:" % pad,
        "%s        from gateway.local_image_persist import durable_text_image_message" % pad,
        "%s        _hip_durable = durable_text_image_message(" % pad,
        "%s            persist_user_message if isinstance(persist_user_message, str) else message_text, event)" % pad,
        "%s        if _hip_durable is not None:" % pad,
        "%s            persist_user_message = _hip_durable" % pad,
        "%s    except Exception as _hip_exc:" % pad,
        "%s        try:" % pad,
        "%s            from gateway.local_image_persist import log" % pad,
        '%s            log("prepare-stage hook failed: %%r" %% (_hip_exc,))' % pad,
        "%s        except Exception:" % pad,
        "%s            pass" % pad,
        "",
    ]
    return "\n".join(lines)


def block_b(indent: str = "            ") -> str:
    pad = indent
    lines = [
        "%s%s queued follow-ups persist caption + @image: refs too (upstream #142500)." % (pad, PATCH_MARKER),
        '%sif not getattr(pending_event, "internal", False):' % pad,
        "%s    try:" % pad,
        "%s        from gateway.local_image_persist import durable_text_image_message" % pad,
        "%s        _hip_next = durable_text_image_message(next_persist_message, pending_event)" % pad,
        "%s        if _hip_next is not None:" % pad,
        "%s            next_persist_message = _hip_next" % pad,
        "%s    except Exception:" % pad,
        "%s        pass" % pad,
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# plan
# ---------------------------------------------------------------------------

class PatchPlan:
    """Computed (not yet applied) plan for one target file."""

    def __init__(self, rel: str):
        self.rel = rel
        self.status = "unpatched"  # unpatched|patched|already|refused|skipped|missing
        self.reason = ""
        self.new_text = None
        self.matched_anchor = ""
        self.removed_legacy = 0

    def as_dict(self):
        return {
            "status": self.status,
            "reason": self.reason,
            "anchor": self.matched_anchor,
            "removed_legacy_blocks": self.removed_legacy,
        }


def _normalize(text: str) -> str:
    return text.replace("\r\n", "\n")


def _denormalize(text: str, original: str) -> str:
    if "\r\n" in original:
        return text.replace("\n", "\r\n")
    return text


def _strip_legacy_blocks(norm: str, plan: PatchPlan) -> str:
    """Remove blocks left by earlier private builds (exact first, then regexes)."""
    if LEGACY_V8_BLOCK_EXACT in norm:
        # The v8 installer appended one newline after the block; drop it too
        # when present so the anchor keeps its original spacing.
        if LEGACY_V8_BLOCK_EXACT + "\n" in norm:
            norm = norm.replace(LEGACY_V8_BLOCK_EXACT + "\n", "", 1)
        else:
            norm = norm.replace(LEGACY_V8_BLOCK_EXACT, "", 1)
        plan.removed_legacy += 1
    else:
        for legacy_re in (LEGACY_V9_PREPARE_REMOVE_RE, LEGACY_V8_REMOVE_RE, LEGACY_V7_REMOVE_RE):
            matches = legacy_re.findall(norm)
            if not matches:
                continue
            if len(matches) > 1:
                raise _Refuse("found %d legacy fix blocks (expected 0 or 1)" % len(matches))
            norm = legacy_re.sub("", norm, count=1)
            plan.removed_legacy += 1
            break
    return norm


class _Refuse(Exception):
    pass


def _structural_insert_prepare(norm: str) -> tuple:
    """Insert after the first ``self._hmwa_apply_message_timestamp(...)`` call,
    balancing parentheses across lines. Returns (patched_text, indent) or raises."""
    lines = norm.split("\n")
    for idx, line in enumerate(lines):
        if "_hmwa_apply_message_timestamp(" not in line:
            continue
        indent = line[: len(line) - len(line.lstrip())]
        depth = 0
        end = idx
        for j in range(idx, min(idx + 6, len(lines))):
            depth += lines[j].count("(") - lines[j].count(")")
            end = j
            if depth <= 0:
                break
        else:
            raise _Refuse("could not balance parentheses around the timestamp call")
        if depth > 0:
            raise _Refuse("could not balance parentheses around the timestamp call")
        patched = "\n".join(lines[: end + 1]) + "\n" + block_a(indent) + "\n".join(lines[end + 1:])
        return patched, indent
    raise _Refuse("no _hmwa_apply_message_timestamp call site found")


def plan_prepare(source: str) -> PatchPlan:
    plan = PatchPlan(TARGET_PREPARE)
    try:
        if PATCH_MARKER in source:
            plan.status = "already"
            return plan
        if "_hmwa_apply_message_timestamp" not in source:
            raise _Refuse("no _hmwa_apply_message_timestamp in module; layout unknown")

        norm = _strip_legacy_blocks(_normalize(source), plan)

        if ANCHOR_A_EXACT in norm:
            plan.matched_anchor = "exact"
            patched = norm.replace(ANCHOR_A_EXACT, ANCHOR_A_EXACT + block_a(), 1)
        else:
            match = ANCHOR_A_FALLBACK.search(norm)
            if match:
                plan.matched_anchor = "whitespace-tolerant"
                patched = norm[: match.end()] + block_a(match.group("indent")) + norm[match.end():]
            else:
                plan.matched_anchor = "structural"
                patched, _indent = _structural_insert_prepare(norm)

        plan.new_text = _denormalize(patched, source)
        plan.status = "patched"
    except _Refuse as exc:
        plan.status = "refused"
        plan.reason = str(exc)
    return plan


def plan_runner(source: str) -> PatchPlan:
    plan = PatchPlan(TARGET_RUNNER)
    marker_b = PATCH_MARKER + " queued follow-ups"
    try:
        if marker_b in source:
            plan.status = "already"
            return plan
        if "strip_discord_triggering_note" not in source:
            plan.status = "skipped"
            plan.reason = "no strip_discord_triggering_note in module (follow-up path moved)"
            return plan

        norm = _normalize(source)
        legacy = LEGACY_V9_RUNNER_REMOVE_RE.findall(norm)
        if legacy:
            if len(legacy) > 1:
                raise _Refuse("found %d legacy fix blocks (expected 0 or 1)" % len(legacy))
            norm = LEGACY_V9_RUNNER_REMOVE_RE.sub("", norm, count=1)
            plan.removed_legacy += 1
        if ANCHOR_B_EXACT in norm:
            plan.matched_anchor = "exact"
            patched = norm.replace(ANCHOR_B_EXACT, ANCHOR_B_EXACT + block_b(), 1)
        else:
            match = ANCHOR_B_FALLBACK.search(norm)
            if match is None:
                plan.status = "skipped"
                plan.reason = "queued follow-up persist line not found in a recognized shape"
                return plan
            plan.matched_anchor = "whitespace-tolerant"
            patched = norm[: match.end()] + block_b(match.group("indent")) + norm[match.end():]

        plan.new_text = _denormalize(patched, source)
        plan.status = "patched"
    except _Refuse as exc:
        plan.status = "refused"
        plan.reason = str(exc)
    return plan


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------

def _compile_text_ok(new_text: str) -> bool:
    tmp = None
    cache = None
    try:
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False, encoding="utf-8", newline="") as fh:
            fh.write(new_text)
            tmp = fh.name
        cache = tempfile.mktemp(suffix=".pyc")
        py_compile.compile(tmp, doraise=True, cfile=cache)
        return True
    except Exception:
        return False
    finally:
        for leftover in (tmp, cache):
            if leftover:
                try:
                    Path(leftover).unlink()
                except Exception:
                    pass


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".hip.tmp")
    cache = tempfile.mktemp(suffix=".pyc")
    tmp.write_text(text, encoding="utf-8", newline="")
    try:
        py_compile.compile(str(tmp), doraise=True, cfile=cache)
        tmp.replace(path)
    except Exception:
        try:
            tmp.unlink()
        except Exception:
            pass
        raise
    finally:
        try:
            Path(cache).unlink()
        except Exception:
            pass


def plan_all(root: Path) -> dict:
    plans = {}
    for rel in TARGETS:
        path = Path(root) / rel
        if not path.is_file():
            plan = PatchPlan(rel)
            plan.status = "missing"
            plan.reason = "file not found"
            plans[rel] = plan
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        plans[rel] = plan_prepare(source) if rel == TARGET_PREPARE else plan_runner(source)
    return plans


def apply_plans(root: Path, plans: dict, backup_dir: Path | None = None) -> dict:
    """Apply computed plans with backup + compile gates. Returns applied info."""
    applied = {}
    for rel, plan in plans.items():
        if plan.status == "already":
            applied[rel] = {"status": "already", "anchor": "kept"}
            continue
        if plan.status != "patched":
            continue
        path = Path(root) / rel
        if not _compile_text_ok(plan.new_text):
            plan.status = "refused"
            plan.reason = "patched module failed py_compile; refusing to write"
            continue
        if backup_dir is not None:
            backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, backup_dir / path.name)
        try:
            _atomic_write(path, plan.new_text)
        except Exception:
            plan.status = "refused"
            plan.reason = "atomic write or compile check failed"
            continue
        applied[rel] = {"status": "patched", "anchor": plan.matched_anchor,
                        "removed_legacy": plan.removed_legacy}
    return applied


def restore_backup(root: Path, backup_dir: Path) -> list:
    restored = []
    for rel in TARGETS:
        backup = Path(backup_dir) / Path(rel).name
        if backup.is_file():
            shutil.copy2(backup, Path(root) / rel)
            restored.append(rel)
    return restored


def install_helper(root: Path) -> bool:
    """Write gateway/local_image_persist.py (idempotent)."""
    gateway = Path(root) / "gateway"
    helper = gateway / HELPER_NAME
    if not gateway.is_dir():
        return False
    try:
        current = helper.read_text(encoding="utf-8") if helper.is_file() else ""
    except Exception:
        current = ""
    source = payload_source.helper_source()
    if current == source:
        return True
    try:
        _atomic_write(helper, source)
        return True
    except Exception:
        return False


def helper_present(root: Path) -> bool:
    return (Path(root) / "gateway" / HELPER_NAME).is_file()


def remove_helper(root: Path) -> bool:
    helper = Path(root) / "gateway" / HELPER_NAME
    try:
        if helper.is_file():
            helper.unlink()
            return True
    except Exception:
        pass
    return False


def markers_in(root: Path) -> dict:
    """Status of every fix marker we know about (current + legacy), for diagnostics."""
    probes = [
        ("gateway/run_turn_prepare.py", PATCH_MARKER, "current write patch (prepare)"),
        ("gateway/run_turn.py", PATCH_MARKER + " queued follow-ups", "current follow-up patch"),
        ("gateway/local_image_persist.py", "def durable_text_image_message", "current helper file"),
        ("gateway/run_turn_prepare.py", "Hermes image persist v9:", "legacy write patch (v9, replaced on upgrade)"),
        ("gateway/run_turn.py", "Hermes image persist v9: queued", "legacy follow-up patch (v9)"),
        ("gateway/run_turn_prepare.py", "Hermes LOCAL text-image persistence v8:", "legacy write patch (v8)"),
        ("gateway/run_turn_prepare.py", "Hermes LOCAL text-image persistence v7:", "legacy write patch (v7)"),
        ("gateway/run_turn.py", "Hermes text-image durable persistence v6", "legacy runner patch (v6)"),
        ("tui_gateway/session_history.py", "Hermes UI display v4", "legacy gateway read patch (v4)"),
        ("hermes_cli/web_routers/sessions.py", "Hermes UI display v4", "legacy REST read patch (v4)"),
        ("hermes_cli/web_routers/sessions.py", "Hermes UI display v5", "legacy REST read patch (v5)"),
        ("hermes_cli/local_runtime/vision_display.py", "Image display v8", "legacy read helper (v8)"),
        ("agent/image_routing.py", "def split_image_notes", "official note parser"),
    ]
    out = {}
    for rel, needle, label in probes:
        path = Path(root) / rel
        present = False
        try:
            if path.is_file():
                present = needle in path.read_text(encoding="utf-8", errors="replace")
        except Exception:
            present = False
        out[label] = {"file": rel, "present": present}
    return out
