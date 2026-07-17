"""Safe constructors for contract envelopes."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Mapping

from .model import ContractEnvelope, MessageKind
from .validation import parse_envelope


def new_request_id() -> str:
    return f"req-{uuid.uuid4()}"


def new_action_id() -> str:
    return f"act-{uuid.uuid4()}"


def utc_timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def make_envelope(
    kind: MessageKind,
    payload: Mapping[str, Any],
    *,
    request_id: str | None = None,
    action_id: str | None = None,
    timestamp: str | None = None,
) -> ContractEnvelope:
    envelope = ContractEnvelope(
        request_id or new_request_id(),
        kind,
        timestamp or utc_timestamp(),
        dict(payload),
        action_id,
    )
    return parse_envelope(envelope.to_dict())
