"""Trusted factories for canonical journal events."""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from .model import EventType, JournalEvent
from .templates import description_for
from .validation import parse_event

COMPONENT_VERSION = "0.1.0a0"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


class EventFactory:
    def __init__(
        self,
        component: str = "guard",
        component_version: str = COMPONENT_VERSION,
        clock: Callable[[], str] = utc_now,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self.component = component
        self.component_version = component_version
        self.clock = clock
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))

    def create(self, event_type: EventType, code: str, **public_fields: Any) -> JournalEvent:
        description = description_for(event_type, code, public_fields)
        event = JournalEvent(
            self.id_factory(), self.clock(), event_type, self.component,
            self.component_version, code, description, dict(public_fields),
        )
        return parse_event(event.to_dict())

    def contract_action(self, calldata: bytes, **public_fields: Any) -> JournalEvent:
        fields = dict(public_fields)
        fields["calldata_hash"] = hashlib.sha256(calldata).hexdigest()
        return self.create(EventType.CONTRACT_ACTION, "CONTRACT_ACTION", **fields)
