"""Versioned, secret-free messages shared across local components."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

SCHEMA_VERSION = "1"


class MessageKind(str, Enum):
    HEALTH_REQUEST = "health_request"
    OPEN_WALLET = "open_wallet"
    READ_WALLET_BALANCES = "read_wallet_balances"
    READ_LENDING_MARKETS = "read_lending_markets"
    READ_LENDING_POSITIONS = "read_lending_positions"
    READ_LENDING_PORTFOLIO = "read_lending_portfolio"
    READ_EARN_PORTFOLIO = "read_earn_portfolio"
    MODULE_READ_REQUEST = "module_read_request"
    MODULE_ACTION_INTENT = "module_action_intent"
    MODULE_AUTHORITY_INTENT = "module_authority_intent"
    MODULE_ACTION_STATUS_REQUEST = "module_action_status_request"
    LENDING_ACTION_INTENT = "lending_action_intent"
    LENDING_AUTHORITY_INTENT = "lending_authority_intent"
    TRANSFER_INTENT = "transfer_intent"
    PREPARE_TRANSFER = "prepare_transfer"
    ACTION_STATUS_REQUEST = "action_status_request"
    CANCEL_ACTION = "cancel_action"
    RECOVER_ACTION = "recover_action"
    HEALTH_RESPONSE = "health_response"
    WALLET_OPENED = "wallet_opened"
    WALLET_BALANCES = "wallet_balances"
    LENDING_MARKETS = "lending_markets"
    LENDING_POSITIONS = "lending_positions"
    LENDING_PORTFOLIO = "lending_portfolio"
    EARN_PORTFOLIO = "earn_portfolio"
    MODULE_READ_RESPONSE = "module_read_response"
    MODULE_ACTION_PREVIEW = "module_action_preview"
    MODULE_ACTION_STATUS = "module_action_status"
    LENDING_ACTION_PREVIEW = "lending_action_preview"
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
