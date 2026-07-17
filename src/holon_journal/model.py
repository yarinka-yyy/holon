"""Strict secret-free journal event model."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

JOURNAL_VERSION = "1"
CORE_FIELDS = frozenset(
    {
        "journal_version", "event_id", "timestamp", "event_type", "component",
        "component_version", "code", "description",
    }
)
OPTIONAL_FIELDS = frozenset(
    {
        "request_id", "action_id", "flow_id", "action_type", "network",
        "wallet_address", "recipient", "asset", "amount_atomic", "policy_version",
        "policy_result", "guard_state", "contract", "selector", "calldata_hash",
        "transaction_hash", "simulated",
    }
)


class EventType(str, Enum):
    POLICY_DECISION = "POLICY_DECISION"
    REFUSAL = "REFUSAL"
    FLOW_STARTED = "FLOW_STARTED"
    ACTION_CANCELLED = "ACTION_CANCELLED"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    RECOVERY_COMPLETED = "RECOVERY_COMPLETED"
    SIGNING_DISABLED = "SIGNING_DISABLED"
    TECHNICAL_ERROR = "TECHNICAL_ERROR"
    REQUEST_BLOCK_STARTED = "REQUEST_BLOCK_STARTED"
    REQUEST_BLOCK_EXPIRED = "REQUEST_BLOCK_EXPIRED"
    REQUEST_BLOCK_CLEARED = "REQUEST_BLOCK_CLEARED"
    LOCAL_APPROVED = "LOCAL_APPROVED"
    LOCAL_REJECTED = "LOCAL_REJECTED"
    BROADCAST_RESULT = "BROADCAST_RESULT"
    CONTRACT_ACTION = "CONTRACT_ACTION"


REQUIRED_BY_TYPE = {
    EventType.POLICY_DECISION: frozenset({"policy_result"}),
    EventType.FLOW_STARTED: frozenset({"action_id", "flow_id", "guard_state"}),
    EventType.ACTION_CANCELLED: frozenset({"action_id", "guard_state"}),
    EventType.RECOVERY_REQUIRED: frozenset({"action_id", "guard_state"}),
    EventType.RECOVERY_COMPLETED: frozenset({"action_id", "guard_state"}),
    EventType.REQUEST_BLOCK_STARTED: frozenset({"action_id", "guard_state"}),
    EventType.LOCAL_APPROVED: frozenset({"action_id"}),
    EventType.LOCAL_REJECTED: frozenset({"action_id"}),
    EventType.BROADCAST_RESULT: frozenset({"action_id"}),
    EventType.CONTRACT_ACTION: frozenset(
        {
            "action_id", "action_type", "network", "asset", "amount_atomic",
            "contract", "selector", "calldata_hash",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class JournalEvent:
    event_id: str
    timestamp: str
    event_type: EventType
    component: str
    component_version: str
    code: str
    description: str
    public_fields: dict[str, Any]
    journal_version: str = JOURNAL_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = {
            "journal_version": self.journal_version,
            "event_id": self.event_id,
            "timestamp": self.timestamp,
            "event_type": self.event_type.value,
            "component": self.component,
            "component_version": self.component_version,
            "code": self.code,
            "description": self.description,
        }
        value.update(self.public_fields)
        return value
