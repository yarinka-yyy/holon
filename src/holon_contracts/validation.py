"""Strict validators for shared contract envelopes and payloads."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any, Mapping

from .codes import RefusalCode, SecurityCode
from .model import SCHEMA_VERSION, ContractEnvelope, MessageKind
from .payloads import validate_payload
from .schemas import (
    ACTION_FIELDS,
    ACTION_OPTIONAL_KINDS,
    ACTION_REQUIRED_KINDS,
    BASE_FIELDS,
)
from .violations import ContractViolation

ID_PATTERN = "^(req|act)-([0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12})$"
TIMESTAMP_PATTERN = r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$"


def _identifier(value: object, prefix: str) -> str:
    code = RefusalCode.ACTION_ID_INVALID if prefix == "act" else RefusalCode.REQUEST_INVALID
    if not isinstance(value, str) or len(value) > 64:
        raise ContractViolation(code.value, "Invalid identifier.")
    match = re.fullmatch(ID_PATTERN, value)
    if match is None or match.group(1) != prefix:
        raise ContractViolation(code.value, "Invalid identifier.")
    if uuid.UUID(match.group(2)).version != 4:
        raise ContractViolation(code.value, "Invalid identifier.")
    return value


def _timestamp(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 32
        or re.fullmatch(TIMESTAMP_PATTERN, value) is None
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid timestamp.")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid timestamp.") from exc
    if parsed.utcoffset() is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid timestamp.")
    return value


def parse_envelope(value: Mapping[str, Any]) -> ContractEnvelope:
    if not isinstance(value, Mapping):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Message must be an object.")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise ContractViolation(SecurityCode.SCHEMA_VERSION_UNSUPPORTED.value, "Schema version is unsupported.")
    try:
        kind = MessageKind(value.get("kind"))
    except (TypeError, ValueError) as exc:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Message kind is unsupported.") from exc
    fields = ACTION_FIELDS if "action_id" in value else BASE_FIELDS
    has_action = "action_id" in value
    action_forbidden = kind not in ACTION_REQUIRED_KINDS | ACTION_OPTIONAL_KINDS
    if set(value) != fields or (kind in ACTION_REQUIRED_KINDS and not has_action):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid message envelope.")
    if has_action and action_forbidden:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid message envelope.")
    payload = value.get("payload")
    if not isinstance(payload, Mapping):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Payload must be an object.")
    request_id = _identifier(value.get("request_id"), "req")
    action_id = _identifier(value.get("action_id"), "act") if "action_id" in value else None
    validate_payload(kind, payload)
    return ContractEnvelope(request_id, kind, _timestamp(value.get("timestamp")), dict(payload), action_id)
