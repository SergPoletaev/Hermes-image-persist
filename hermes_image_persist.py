"""Hermes Image Persist - runtime shim (update-immune layer).

This module is planted into the site-packages of the Python environment the
Hermes gateway actually runs on (the PM "committed generation" venv, any sibling
generations, and legacy in-tree venvs) together with a ``.pth`` line that
imports it at interpreter startup. ``hermes_bootstrap.activate_dependencies``
puts that site-packages on ``sys.path`` via ``site.addsitedir()``, which
processes ``.pth`` files - so the shim loads in every Hermes process (gateway,
web server, TUI sidecar) even though the launchers run ``python -I`` (isolated
mode still imports ``site``).

What it does
------------
Registers a meta-path import hook for three Hermes modules and, right after
each is imported, wraps exactly one seam with the durable-text fix for
GitHub issue NousResearch/hermes-agent#142500 (text-mode image turns persist
the vision-enrichment prose, so the Desktop loses the pasted thumbnail):

1. ``gateway.run_turn_prepare`` -> wrap
   ``GatewayTurnPrepareMixin._hmwa_apply_message_timestamp`` so the *persist*
   leg of its result becomes ``caption + @image: refs`` instead of the
   enrichment note. The model-facing ``message_text`` is never modified.
2. ``gateway.run_inbound`` -> wrap module-level ``strip_discord_triggering_note``
   (the same persist seam the queued follow-up path in ``run_turn.py`` uses).
   Idempotent with (1): after either seam swaps the text it is no longer
   note-shaped, so the other becomes a no-op.
3. ``hermes_cli.web_routers.sessions`` -> wrap ``_project_for_display`` so
   legacy rows that were already stored with the enrichment note get a
   display-only ``display_content`` projection (caption + ``@image:`` refs).
   The stored content itself is never rewritten.

Design rules
------------
- Pure stdlib, Python 3.9+.
- NEVER raises: every entry point is wrapped in try/except and failures are
  logged (best-effort) with the ``Image persistence:`` marker.
- NEVER modifies what the model sees; only the durable/display projections.
- Only rewrites text that starts with a known enrichment-note prefix AND has
  at least one referenced image file that actually exists on disk.
- Platform-agnostic (the upstream fix for #142500 is too); the platform value
  is only logged. Set ``HERMES_IMAGE_PERSIST=off`` to disable at runtime.

This file is self-contained on purpose: it must run from bare site-packages
before Hermes packages are importable.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys

__version__ = "1.0.3"

_MARKER = "Image persistence"
_READ_MARKER = "Image display"
_SHIM_FLAG = "_hermes_image_persist_installed"

NOTE_PREFIXES = (
    "[The user sent an image",
    "[The user attached an image",
    "[The user shared an image",
    "[The user provided an image",
    "[The user uploaded an image",
)

# One enrichment prefix, from its opening bracket to the closing "~]" of the
# block that carries the image_url hint (0.23.2 emits two adjacent blocks: a
# description block ending in "]" and a hint block ending in "~]"; the lazy
# match spans both, exactly like upstream's _IMAGE_NOTE_RE).
NOTE_BLOCK_RE = re.compile(
    r"\[\s*The user (?:sent|attached|shared|provided|uploaded) an image\b"
    r"[\s\S]*?image_url:[^\n]*?~\][^\n]*",
)

# Lone hint block without a note-prefixed opener (defensive).
HINT_BLOCK_RE = re.compile(r"\n?\[If you need a closer look[^\n]*\]")

# ``image_url: <path>`` inside a note; keep spaces in the path (upstream cache
# paths can live under user directories with spaces).
IMAGE_URL_LINE_RE = re.compile(r"image_url:\s*([^\n]+?)(?:\s*~)?\s*\][^\n]*", re.IGNORECASE)

IMAGE_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".tiff", ".tif", ".heic", ".heif", ".avif", ".jfif",
)

_MAX_IMAGES = 12
_MAX_PATH_LEN = 1024
_MAX_LOG_BYTES = 262144


# --------------------------------------------------------------------------
# logging (best-effort, never raises)
# --------------------------------------------------------------------------

def _hermes_home() -> str:
    env = os.environ.get("HERMES_HOME", "").strip()
    if env:
        return env
    try:
        if os.name == "nt":
            base = os.environ.get("LOCALAPPDATA", "").strip()
            if not base:
                base = os.path.join(os.path.expanduser("~"), "AppData", "Local")
            return os.path.join(base, "hermes")
        return os.path.join(os.path.expanduser("~"), ".hermes")
    except Exception:
        return ""


def _log(message: str, read_path: bool = False) -> None:
    marker = _READ_MARKER if read_path else _MARKER
    try:
        import logging

        logging.getLogger("hermes_image_persist").info("%s: %s", marker, message)
    except Exception:
        pass
    try:  # sidecar file for Inspect/guard diagnostics; failures are silent
        home = _hermes_home()
        if home:
            path = os.path.join(home, "hermes-image-persist", "shim.log")
            try:
                if os.path.isfile(path) and os.path.getsize(path) > _MAX_LOG_BYTES:
                    os.replace(path, path + ".1")
            except OSError:
                pass
            import time

            with open(path, "a", encoding="utf-8", errors="replace") as fh:
                fh.write("%s %s[%s]: %s\n" % (
                    time.strftime("%Y-%m-%d %H:%M:%S"), marker,
                    os.path.basename(sys.argv[0] or "python"), message))
    except Exception:
        pass


def _disabled_by_env() -> bool:
    try:
        return os.environ.get("HERMES_IMAGE_PERSIST", "").strip().lower() in ("off", "0", "false", "disabled")
    except Exception:
        return False


# --------------------------------------------------------------------------
# shared payload logic (durable text builder)
# --------------------------------------------------------------------------

def _platform_value(source_or_event) -> str:
    obj = source_or_event
    platform = getattr(obj, "platform", None)
    if platform is None and obj is not None:
        source = getattr(obj, "source", None)
        platform = getattr(source, "platform", None)
    value = getattr(platform, "value", platform)
    try:
        return str(value or "unknown").strip().lower() or "unknown"
    except Exception:
        return "unknown"


def _norm(p: str) -> str:
    return os.path.normcase(os.path.normpath(p))


def _clean_path(raw: str) -> str:
    p = (raw or "").strip()
    for wrapper in ("<", ">", '"', "'", "`"):
        if len(p) >= 2 and p.startswith(wrapper) and p.endswith(wrapper):
            p = p[1:-1].strip()
    if p[:7].lower() == "file://":
        p = p[7:]
        # file:///C:/... -> C:/...; file:////tmp/x or file://tmp/x -> /tmp/x
        # (empty host, path form); never leave a "//"-prefixed POSIX path in
        # the durable ref - os.path.isfile() tolerates it, the Desktop won't.
        if os.name != "nt":
            while p.startswith("//"):
                p = p[1:]
        elif len(p) >= 3 and p[0] == "/" and p[2] == ":":
            p = p[1:]
        elif p.startswith("/"):
            p = p[1:]  # file://host/share -> host/share is out of scope; best effort
    return p


def _expand_note_paths(candidates, note_text) -> list:
    """v1.0.1: resolve bare file names inside the note against event paths.

    Inbound pre-analysis may rewrite attachment paths into cached copies under
    a per-session cache dir while the note keeps the ORIGINAL bare file name
    (or the note carries the bare name while the event holds the absolute
    path). A bare name that exists next to an absolute candidate is accepted
    as that file; a bare name alone is still tried from the process CWD.
    """
    paths = list(candidates or ())
    try:
        note_text = note_text if isinstance(note_text, str) else ""
        if not note_text:
            return paths
        for match in IMAGE_URL_LINE_RE.finditer(note_text):
            p = _clean_path(match.group(1))
            if not p or os.path.isabs(p):
                continue
            for anchor in candidates or ():
                if not isinstance(anchor, str) or not os.path.isabs(anchor):
                    continue
                sibling = os.path.join(os.path.dirname(anchor), p)
                if os.path.isfile(sibling):
                    paths.append(sibling)
                    break
            else:
                paths.append(p)
    except Exception:
        pass
    return paths


def _existing_image_paths(candidates) -> list:
    """Unique, on-disk image paths, first-seen order."""
    out, seen = [], set()
    for raw in candidates or ():
        if not isinstance(raw, str):
            continue
        p = _clean_path(raw)
        if not p or len(p) > _MAX_PATH_LEN:
            continue
        if not p.lower().endswith(IMAGE_SUFFIXES):
            continue
        key = _norm(p)
        if key in seen:
            continue
        try:
            if not os.path.isfile(p):
                continue
        except Exception:
            continue
        seen.add(key)
        out.append(p)
    return out


def _collect_event_media_paths(event) -> list:
    """Attachment paths from the inbound event, duck-typed across shapes."""
    paths = []
    for attr in ("media_urls", "image_urls", "images", "attachments", "files"):
        try:
            value = getattr(event, attr, None)
        except Exception:
            continue
        if not value:
            continue
        if isinstance(value, str):
            paths.append(value)
            continue
        if isinstance(value, dict):
            value = list(value.values())
        if not isinstance(value, (list, tuple, set)):
            continue
        for item in value:
            if isinstance(item, str):
                paths.append(item)
                continue
            if item is None:
                continue
            for sub in ("path", "url", "file", "filename", "local_path", "file_path"):
                sub_value = getattr(item, sub, None)
                if isinstance(sub_value, str) and sub_value:
                    paths.append(sub_value)
    return paths


def _collect_note_paths(model_message: str) -> list:
    """Image paths referenced inside the enrichment note itself."""
    paths = []
    try:
        from agent.image_routing import split_image_notes

        _caption, refs = split_image_notes(model_message)
        if isinstance(refs, (list, tuple)):
            paths.extend(refs)
    except Exception:
        pass
    for match in IMAGE_URL_LINE_RE.finditer(model_message):
        paths.append(match.group(1))
    return paths


def _note_caption_fallback(model_message: str) -> str:
    """Caption with every enrichment block stripped, without the official parser."""
    stripped = NOTE_BLOCK_RE.sub(" ", model_message)
    stripped = HINT_BLOCK_RE.sub(" ", stripped)
    return " ".join(stripped.split()).strip()


def _format_ref(path: str) -> str:
    try:
        from agent.context_references import format_reference_value

        formatted = format_reference_value(path)
        if isinstance(formatted, str) and formatted.strip():
            return formatted
    except Exception:
        pass
    if re.search(r"[\s()\[\]{}<>\"'`]", path):
        for quote in ("`", '"', "'"):
            if quote not in path:
                return quote + path + quote
    return path


def _looks_like_note(text) -> bool:
    return isinstance(text, str) and text.lstrip().startswith(NOTE_PREFIXES)


def durable_text_image_message(model_message, event):
    """Return ``caption + @image: refs`` for a text-mode image turn, else None.

    None means "not a provable text-mode image turn" and the caller keeps the
    default persistence. Only real, existing local image files can become
    references. Mirrors upstream ``_build_persist_message_with_image_refs``
    (single newline between caption and refs).
    """
    try:
        if not _looks_like_note(model_message):
            return None
        if getattr(event, "internal", False):
            return None  # self-injected turns are timeline notices, not user bubbles
        event_paths = _collect_event_media_paths(event)
        note_paths = _collect_note_paths(model_message)
        merged = list(event_paths)
        for p in note_paths:
            if p not in merged:
                merged.append(p)
        # v1.0.1: bare file names in the note resolve against event paths / CWD.
        merged = _expand_note_paths(merged, model_message)
        durable = _existing_image_paths(merged)
        if not durable:
            _log("text-image turn recognized but no durable image file found "
                 "(event_media=%d, note_refs=%d, platform=%s)"
                 % (len(event_paths), len(note_paths), _platform_value(event)))
            return None
        if len(durable) > _MAX_IMAGES:
            durable = durable[:_MAX_IMAGES]
        caption = ""
        raw_text = getattr(event, "text", None)
        if isinstance(raw_text, str):
            caption = raw_text.strip()
        if not caption:
            caption = _note_caption_fallback(model_message)
        refs = "\n".join("@image:%s" % _format_ref(p) for p in durable)
        result = "%s\n%s" % (caption, refs) if caption else refs
        _log("durable image references set (images=%d, caption=%s, platform=%s)"
             % (len(durable), "yes" if caption else "none", _platform_value(event)))
        return result
    except Exception as exc:
        _log("helper error (%r); keeping default persistence" % (exc,))
        return None


def image_note_display_text(content):
    """Read-path projection of a stored enrichment note (caption + refs), else None."""
    try:
        if not _looks_like_note(content):
            return None
        durable = _existing_image_paths(_collect_note_paths(content))
        if not durable:
            _log("stored note has no live image file; refusing to invent a preview", read_path=True)
            return None
        if len(durable) > _MAX_IMAGES:
            durable = durable[:_MAX_IMAGES]
        refs = "\n".join("@image:%s" % _format_ref(p) for p in durable)
        caption = _note_caption_fallback(content)
        _log("projected %d image ref(s) for stored row" % len(durable), read_path=True)
        return ("%s\n%s" % (caption, refs)).strip() if caption else refs
    except Exception as exc:
        _log("read helper error (%r); leaving row untouched" % (exc,), read_path=True)
        return None


# --------------------------------------------------------------------------
# seam wrappers
# --------------------------------------------------------------------------

def _wrap_timestamp_method(module) -> None:
    mixin = getattr(module, "GatewayTurnPrepareMixin", None)
    original = getattr(mixin, "_hmwa_apply_message_timestamp", None)
    if mixin is None or original is None or getattr(original, "_hip_patched", False):
        _log("run_turn_prepare seam not found or already wrapped (mixin=%s, method=%s)"
             % (mixin is not None, original is not None))
        return

    def wrapper(self, event, message_text, _original=original):
        message_text, persist_user_message, persist_user_timestamp = _original(self, event, message_text)
        try:
            if not getattr(event, "internal", False):
                durable = durable_text_image_message(
                    persist_user_message if isinstance(persist_user_message, str) else message_text, event)
                if durable is not None:
                    persist_user_message = durable
        except Exception as exc:
            _log("prepare seam failed (%r); keeping default persistence" % (exc,))
        return message_text, persist_user_message, persist_user_timestamp

    wrapper._hip_patched = True
    wrapper.__wrapped__ = original
    mixin._hmwa_apply_message_timestamp = wrapper
    _log("write seam active: GatewayTurnPrepareMixin._hmwa_apply_message_timestamp wrapped")


def _wrap_strip_discord_note(module) -> None:
    original = getattr(module, "strip_discord_triggering_note", None)
    if original is None or getattr(original, "_hip_patched", False):
        _log("run_inbound seam not found or already wrapped (fn=%s)" % (original is not None))
        return

    def wrapper(event, message_text, _original=original):
        out = _original(event, message_text)
        try:
            if not getattr(event, "internal", False):
                durable = durable_text_image_message(
                    out if isinstance(out, str) else message_text, event)
                if durable is not None:
                    out = durable
        except Exception as exc:
            _log("inbound seam failed (%r); keeping default persistence" % (exc,))
        return out

    wrapper._hip_patched = True
    wrapper.__wrapped__ = original
    module.strip_discord_triggering_note = wrapper
    _log("write seam active: gateway.run_inbound.strip_discord_triggering_note wrapped")


def _wrap_strip_discord_note_in_module(module) -> None:
    """v1.0.1: the SAME seam, imported into ``gateway.run_turn`` (queued follow-ups).

    ``run_turn.py`` does ``from gateway.run_inbound import strip_discord_triggering_note``
    at call time (inside the follow-up branch), so rebinding the function on the
    run_inbound module already covers it. This wrapper exists for builds where
    the import was hoisted to module level (a from-import binds the name in
    run_turn's namespace at import time, before either shim hook ran). If the
    name is absent - nothing to do.
    """
    original = getattr(module, "strip_discord_triggering_note", None)
    if original is None or getattr(original, "_hip_patched", False):
        return

    def wrapper(event, message_text, _original=original):
        out = _original(event, message_text)
        try:
            if not getattr(event, "internal", False):
                durable = durable_text_image_message(
                    out if isinstance(out, str) else message_text, event)
                if durable is not None:
                    out = durable
        except Exception as exc:
            _log("run_turn seam failed (%r); keeping default persistence" % (exc,))
        return out

    wrapper._hip_patched = True
    wrapper.__wrapped__ = original
    module.strip_discord_triggering_note = wrapper
    _log("write seam active: gateway.run_turn.strip_discord_triggering_note wrapped")


def _wrap_project_for_display(module) -> None:
    original = getattr(module, "_project_for_display", None)
    if original is None or getattr(original, "_hip_patched", False):
        _log("sessions router seam not found or already wrapped (fn=%s)" % (original is not None))
        return

    def wrapper(messages, *args, _original=original, **kwargs):
        projected = _original(messages, *args, **kwargs)
        try:
            for row in projected:
                if not isinstance(row, dict) or row.get("role") != "user":
                    continue
                if row.get("display_kind") or row.get("display_content") is not None:
                    continue
                content = row.get("content")
                if not _looks_like_note(content):
                    continue
                display = image_note_display_text(content)
                if display is not None:
                    row["display_content"] = display
        except Exception as exc:
            _log("display projection failed (%r); rows untouched" % (exc,), read_path=True)
        return projected

    wrapper._hip_patched = True
    wrapper.__wrapped__ = original
    module._project_for_display = wrapper
    _log("read seam active: web_routers.sessions._project_for_display wrapped")


_TARGETS = {
    "gateway.run_turn_prepare": _wrap_timestamp_method,
    "gateway.run_inbound": _wrap_strip_discord_note,
    "gateway.run_turn": _wrap_strip_discord_note_in_module,
    "hermes_cli.web_routers.sessions": _wrap_project_for_display,
}


# --------------------------------------------------------------------------
# import hook
# --------------------------------------------------------------------------

class _WrapLoader:
    """Delegate loader that patches the module right after it is executed."""

    def __init__(self, loader, patcher):
        self._loader = loader
        self._patcher = patcher

    def create_module(self, spec):
        return self._loader.create_module(spec)

    def exec_module(self, module):
        self._loader.exec_module(module)
        try:
            self._patcher(module)
        except Exception as exc:
            _log("post-import patch of %s failed (%r)" % (getattr(module, "__name__", "?"), exc))

    def __getattr__(self, name):
        return getattr(self._loader, name)


class _HipFinder:
    """Meta-path finder that only intercepts the three target modules."""

    def find_spec(self, fullname, path=None, target=None):
        patcher = _TARGETS.get(fullname)
        if patcher is None:
            return None

        already = sys.modules.get(fullname)
        if already is not None and getattr(already, "__hip_patched__", False):
            return None
        if already is not None:
            # Imported before our hook saw it (late shim install): patch in place.
            try:
                patcher(already)
                already.__hip_patched__ = True
            except Exception as exc:
                _log("late patch of %s failed (%r)" % (fullname, exc))
            return None

        sys.meta_path.remove(self)
        try:
            spec = importlib.util.find_spec(fullname)
        except Exception:
            spec = None
        finally:
            sys.meta_path.insert(0, self)
        if spec is None or spec.loader is None:
            _log("target module %s not importable here; seam idle in this process" % fullname)
            return None

        def _mark_and_patch(module, _patcher=patcher):
            _patcher(module)
            try:
                module.__hip_patched__ = True
            except Exception:
                pass

        spec.loader = _WrapLoader(spec.loader, _mark_and_patch)
        return spec


def _install() -> None:
    if _disabled_by_env():
        _log("disabled via HERMES_IMAGE_PERSIST env; no seams installed")
        return
    if getattr(sys, _SHIM_FLAG, False):
        return  # second .pth pass (activate_dependencies re-runs addsitedir)
    try:
        sys.meta_path.insert(0, _HipFinder())
        setattr(sys, _SHIM_FLAG, True)
        _log("shim installed (v%s, pid=%s)" % (__version__, os.getpid()))
    except Exception as exc:
        _log("install failed (%r); Hermes runs unpatched" % (exc,))


try:
    _install()
except Exception:  # absolute last resort: never break the host process
    pass
