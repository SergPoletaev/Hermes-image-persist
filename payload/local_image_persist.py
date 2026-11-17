"""Hermes Image Persist - durable user text for text-mode image turns.

Installed by the Hermes Image Persist patcher as a new, inert file inside
``gateway/``. The source patch in ``run_turn_prepare.py`` / ``run_turn.py``
calls ``durable_text_image_message`` right after the persist text is
derived; the runtime shim (site-packages) does the same job without touching
sources, and the two are idempotent against each other: after either swaps
the text it is no longer note-shaped, so the other becomes a no-op.

The model-facing ``message_text`` is NEVER modified by this module. Only the
durable transcript row becomes ``caption + @image: refs`` - the same shape the
tui_gateway submit path persists and the Desktop renders thumbnails from
(upstream issue NousResearch/hermes-agent#142500).
"""
from __future__ import annotations

import os
import re

NOTE_PREFIXES = (
    "[The user sent an image",
    "[The user attached an image",
    "[The user shared an image",
    "[The user provided an image",
    "[The user uploaded an image",
)

# One enrichment prefix, opening bracket to the "~]" of the block carrying the
# image_url hint (0.23.2 emits two adjacent blocks; the lazy match spans both,
# exactly like upstream agent.image_routing._IMAGE_NOTE_RE).
NOTE_BLOCK_RE = re.compile(
    r"\[\s*The user (?:sent|attached|shared|provided|uploaded) an image\b"
    r"[\s\S]*?image_url:[^\n]*?~\][^\n]*",
)

# Lone hint block without a note-prefixed opener (defensive).
HINT_BLOCK_RE = re.compile(r"\n?\[If you need a closer look[^\n]*\]")

# "image_url: <path>" inside a note; spaces in the path are preserved.
IMAGE_URL_LINE_RE = re.compile(r"image_url:\s*([^\n]+?)(?:\s*~)?\s*\][^\n]*", re.IGNORECASE)

IMAGE_SUFFIXES = (
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp",
    ".tiff", ".tif", ".heic", ".heif", ".avif", ".jfif",
)

_MAX_IMAGES = 12
_MAX_PATH_LEN = 1024

_LOGGER_NAME = "gateway.local_image_persist"


def log(message: str) -> None:
    """Best-effort INFO log; never raises, works without logging config."""
    try:
        import logging

        logging.getLogger(_LOGGER_NAME).info("Image persistence: %s", message)
    except Exception:
        pass


def _platform_value(event) -> str:
    platform = getattr(event, "platform", None)
    if platform is None:
        source = getattr(event, "source", None)
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
    return p


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

    None means "not a provable text-mode image turn" and the gateway keeps its
    default persistence. Only real, existing local image files can become
    references. Mirrors upstream ``_build_persist_message_with_image_refs``.
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
        durable = _existing_image_paths(merged)
        if not durable:
            log("text-image turn recognized but no durable image file found "
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
        log("durable image references set (images=%d, caption=%s, platform=%s)"
           % (len(durable), "yes" if caption else "none", _platform_value(event)))
        return result
    except Exception as exc:
        log("helper error (%r); keeping default persistence" % (exc,))
        return None
