"""Strict event-envelope parsing and validation."""

from __future__ import annotations

from typing import Any, Mapping

from .model import (
    CORE_FIELDS, JOURNAL_VERSION, OPTIONAL_FIELDS, REQUIRED_BY_TYPE, EventType, JournalEvent,
)
from .rules import JournalValidationError, validate_core, validate_optional
from .templates import description_for


def parse_event(value: Mapping[str, Any]) -> JournalEvent:
    if not isinstance(value, Mapping):
        raise JournalValidationError("Journal event must be an object")
    fields = set(value)
    if not CORE_FIELDS <= fields or fields - CORE_FIELDS - OPTIONAL_FIELDS:
        raise JournalValidationError("Invalid journal event fields")
    if value.get("journal_version") != JOURNAL_VERSION:
        raise JournalValidationError("Unsupported journal version")
    try:
        event_type = EventType(value.get("event_type"))
    except (TypeError, ValueError) as exc:
        raise JournalValidationError("Invalid journal event type") from exc
    for name in ("event_id", "timestamp", "component", "component_version", "code"):
        validate_core(name, value.get(name))
    description = value.get("description")
    if not isinstance(description, str) or not description or len(description) > 512:
        raise JournalValidationError("Invalid journal description")
    if any(character in description for character in "\r\n\t"):
        raise JournalValidationError("Invalid journal description")
    public_fields = {name: value[name] for name in fields - CORE_FIELDS}
    if not REQUIRED_BY_TYPE.get(event_type, frozenset()) <= public_fields.keys():
        raise JournalValidationError("Journal event lacks required public fields")
    for name, item in public_fields.items():
        validate_optional(name, item)
    expected = description_for(event_type, value["code"], public_fields)
    if description != expected:
        raise JournalValidationError("Journal description is not canonical")
    return JournalEvent(
        value["event_id"], value["timestamp"], event_type, value["component"],
        value["component_version"], value["code"], description, public_fields,
    )
