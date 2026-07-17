"""Versioned, secret-free messages shared across local components."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

SCHEMA_VERSION = "1"


class MessageKind(str, Enum):
    HEALTH_REQUEST = "health_request"
    PREPARE_TRANSFER = "prepare_transfer"
    ACTION_STATUS_REQUEST = "action_status_request"
    CANCEL_ACTION = "cancel_action"
    RECOVER_ACTION = "recover_action"
    HEALTH_RESPONSE = "health_response"
    PROTECTED_FLOW_STARTED = "protected_flow_started"
    ACTION_STATUS = "action_status"
    REFUSAL = "refusal"
    ERROR = "error"
    RECOVERY_REQUIRED = "recovery_required"
    SIGNING_DISABLED = "signing_disabled"
    COMPATIBILITY_STATUS = "compatibility_status"


class ActionState(str, Enum):
    READY = "READY"
    PREPARING = "PREPARING"
    AWAITING_LOCAL_CONFIRMATION = "AWAITING_LOCAL_CONFIRMATION"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    REFUSED = "REFUSED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"


@dataclass(frozen=True, slots=True)
class ContractEnvelope:
    request_id: str
    kind: MessageKind
    timestamp: str
    payload: dict[str, Any]
    action_id: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        value: dict[str, Any] = {
            "schema_version": self.schema_version,
            "request_id": self.request_id,
            "kind": self.kind.value,
            "timestamp": self.timestamp,
            "payload": dict(self.payload),
        }
        if self.action_id is not None:
            value["action_id"] = self.action_id
        return value
