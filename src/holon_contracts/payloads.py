"""Strict request and safe-response payload validation."""

from __future__ import annotations

import re
from typing import Any, Mapping

from .codes import RefusalCode
from .model import SCHEMA_VERSION, ActionState, MessageKind
from .schemas import PAYLOAD_FIELDS
from .violations import ContractViolation

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,77}$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
FLOW_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
DANGEROUS_FIELDS = frozenset({"contract", "method", "selector", "calldata", "value"})
GUARD_STATES = frozenset(
    {"NORMAL", "ENTERING", "ACTIVE", "EXITING", "RECOVERY_REQUIRED", "SIGNING_DISABLED"}
)


def _transfer(payload: Mapping[str, Any]) -> None:
    if DANGEROUS_FIELDS & set(payload):
        raise ContractViolation(RefusalCode.ARBITRARY_CALL_REFUSED.value, "Arbitrary calls are refused.")
    expected = PAYLOAD_FIELDS[MessageKind.PREPARE_TRANSFER]
    if "max_total_fee_wei" not in payload:
        raise ContractViolation(RefusalCode.MAX_FEE_REQUIRED.value, "Maximum fee is required.")
    if set(payload) != expected:
        code = RefusalCode.UNKNOWN_AUTHORITY_FIELD if set(payload) - expected else RefusalCode.REQUEST_INVALID
        raise ContractViolation(code.value, "Invalid authority fields.")
    if payload.get("action_type") != "transfer":
        raise ContractViolation(RefusalCode.ACTION_NOT_ALLOWED.value, "Action is not supported.")
    version = payload.get("policy_version")
    if not isinstance(version, str) or not version.isdigit() or len(version) > 8:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid policy version.")
    for field in ("network", "asset"):
        if not isinstance(payload.get(field), str) or NAME_RE.fullmatch(payload[field]) is None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid transfer field.")
    for field in ("amount_atomic", "max_total_fee_wei"):
        if not isinstance(payload.get(field), str) or DECIMAL_RE.fullmatch(payload[field]) is None:
            code = RefusalCode.MAX_FEE_REQUIRED if field.startswith("max_") else RefusalCode.AMOUNT_INVALID
            raise ContractViolation(code.value, "Invalid bounded amount.")
    recipient = payload.get("recipient")
    if not isinstance(recipient, str) or ADDRESS_RE.fullmatch(recipient) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid recipient.")


def _safe_text(payload: Mapping[str, Any]) -> None:
    code = payload.get("code")
    message = payload.get("message")
    if not isinstance(code, str) or CODE_RE.fullmatch(code) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid response code.")
    if not isinstance(message, str) or not message or len(message) > 256:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid response text.")


def _response(kind: MessageKind, payload: Mapping[str, Any]) -> None:
    _safe_text(payload)
    if kind in {MessageKind.REFUSAL, MessageKind.ERROR}:
        if type(payload.get("retryable")) is not bool:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid retry status.")
        return
    state = payload.get("guard_state")
    if state not in GUARD_STATES:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Guard state.")
    if kind in {MessageKind.PROTECTED_FLOW_STARTED, MessageKind.ACTION_STATUS, MessageKind.RECOVERY_REQUIRED}:
        try:
            ActionState(payload.get("action_state"))
        except (TypeError, ValueError) as exc:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid action state.") from exc
        flow_id = payload.get("flow_id")
        if flow_id is not None and (not isinstance(flow_id, str) or FLOW_RE.fullmatch(flow_id) is None):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid flow identifier.")
    if "authority_available" in payload and type(payload["authority_available"]) is not bool:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid authority status.")
    if kind is MessageKind.HEALTH_RESPONSE and payload.get("compatibility") not in {"COMPATIBLE", "INCOMPATIBLE"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid compatibility status.")
    if kind is MessageKind.WALLET_OPENED and payload.get("wallet_state") not in {
        "OPENED", "ACTIVATED",
    }:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Wallet state.")
    if kind is MessageKind.WALLET_OPENED and payload.get("authority_available") is not False:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid authority status.")
    if kind is MessageKind.COMPATIBILITY_STATUS:
        if payload.get("supported_schema_versions") != [SCHEMA_VERSION]:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid schema list.")
        version = payload.get("policy_version")
        if not isinstance(version, str) or not version.isdigit():
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid policy version.")


def validate_payload(kind: MessageKind, payload: Mapping[str, Any]) -> None:
    if kind is MessageKind.PREPARE_TRANSFER:
        _transfer(payload)
        return
    if set(payload) != PAYLOAD_FIELDS[kind]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid message payload.")
    if payload:
        _response(kind, payload)
