"""Field-level validation rules for public journal data."""

from __future__ import annotations

import re
import uuid
from datetime import datetime
from typing import Any

ID_RE = re.compile(r"^(req|act)-([0-9a-f-]{36})$")
UUID_RE = re.compile(r"^[0-9a-f-]{36}$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?Z$")
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
VERSION_RE = re.compile(r"^[0-9A-Za-z][0-9A-Za-z.+-]{0,31}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,77}$")
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
TX_RE = re.compile(r"^0x[0-9A-Fa-f]{64}$")
SELECTOR_RE = re.compile(r"^0x[0-9a-f]{8}$")
GUARD_STATES = {"NORMAL", "ENTERING", "ACTIVE", "EXITING", "RECOVERY_REQUIRED", "SIGNING_DISABLED"}
POLICY_RESULTS = {"ALLOWED", "REFUSED", "ERROR", "NOT_APPLICABLE"}


class JournalValidationError(ValueError):
    pass


def _uuid(value: Any, pattern: re.Pattern[str], group: int = 0) -> None:
    if not isinstance(value, str) or pattern.fullmatch(value) is None:
        raise JournalValidationError("Invalid journal identifier")
    raw = pattern.fullmatch(value).group(group)
    try:
        if uuid.UUID(raw).version != 4:
            raise JournalValidationError("Invalid journal identifier")
    except ValueError as exc:
        raise JournalValidationError("Invalid journal identifier") from exc


def validate_core(name: str, value: Any) -> None:
    if name == "event_id":
        _uuid(value, UUID_RE)
    elif name == "timestamp":
        if not isinstance(value, str) or TIME_RE.fullmatch(value) is None:
            raise JournalValidationError("Invalid journal timestamp")
        try:
            datetime.fromisoformat(value[:-1] + "+00:00")
        except ValueError as exc:
            raise JournalValidationError("Invalid journal timestamp") from exc
    elif name == "component" and (not isinstance(value, str) or NAME_RE.fullmatch(value) is None):
        raise JournalValidationError("Invalid journal component")
    elif name == "component_version" and (
        not isinstance(value, str) or VERSION_RE.fullmatch(value) is None
    ):
        raise JournalValidationError("Invalid component version")
    elif name == "code" and (not isinstance(value, str) or CODE_RE.fullmatch(value) is None):
        raise JournalValidationError("Invalid journal code")


def validate_optional(name: str, value: Any) -> None:
    if name in {"request_id", "action_id"}:
        _uuid(value, ID_RE, 2)
        expected = "req" if name == "request_id" else "act"
        if ID_RE.fullmatch(value).group(1) != expected:
            raise JournalValidationError("Invalid journal identifier")
    elif name == "flow_id":
        _uuid(value, UUID_RE)
    elif name in {"wallet_address", "recipient", "contract"}:
        if not isinstance(value, str) or ADDRESS_RE.fullmatch(value) is None:
            raise JournalValidationError("Invalid public address")
    elif name in {"action_type", "network", "asset"}:
        if not isinstance(value, str) or NAME_RE.fullmatch(value) is None:
            raise JournalValidationError("Invalid public name")
    elif name == "amount_atomic" and (
        not isinstance(value, str) or DECIMAL_RE.fullmatch(value) is None
    ):
        raise JournalValidationError("Invalid public amount")
    elif name == "policy_version" and (
        not isinstance(value, str) or not value.isdigit() or len(value) > 8
    ):
        raise JournalValidationError("Invalid policy version")
    elif name == "policy_result" and (
        not isinstance(value, str) or value not in POLICY_RESULTS
    ):
        raise JournalValidationError("Invalid policy result")
    elif name == "guard_state" and (not isinstance(value, str) or value not in GUARD_STATES):
        raise JournalValidationError("Invalid Guard state")
    elif name == "selector" and (not isinstance(value, str) or SELECTOR_RE.fullmatch(value) is None):
        raise JournalValidationError("Invalid method selector")
    elif name == "calldata_hash" and (not isinstance(value, str) or HASH_RE.fullmatch(value) is None):
        raise JournalValidationError("Invalid calldata hash")
    elif name == "transaction_hash" and (not isinstance(value, str) or TX_RE.fullmatch(value) is None):
        raise JournalValidationError("Invalid transaction hash")
    elif name == "simulated" and type(value) is not bool:
        raise JournalValidationError("Invalid simulated marker")
