"""Bounded JSONL event encoding."""

from __future__ import annotations

import json

from .model import JournalEvent
from .rules import JournalValidationError
from .validation import parse_event

MAX_EVENT_BYTES = 4 * 1024


def encode_event(event: JournalEvent) -> bytes:
    event = parse_event(event.to_dict())
    raw = json.dumps(
        event.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8") + b"\n"
    if len(raw) > MAX_EVENT_BYTES:
        raise JournalValidationError("Journal event is oversized")
    return raw


def decode_event(raw: bytes) -> JournalEvent:
    if not raw or len(raw) > MAX_EVENT_BYTES or not raw.endswith(b"\n"):
        raise JournalValidationError("Invalid journal event size")
    try:
        value = json.loads(raw[:-1].decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JournalValidationError("Invalid journal JSON") from exc
    return parse_event(value)
