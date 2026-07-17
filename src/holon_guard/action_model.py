"""Persistent current and terminal authority-action records."""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from holon_contracts import ActionState

ACTION_STATE_VERSION = 1
MAX_TERMINAL_ACTIONS = 4096
SNAPSHOT_FIELDS = frozenset({"state_version", "current", "terminal"})
RECORD_FIELDS = frozenset({"action_id", "fingerprint", "state", "code", "updated_at"})
ACTION_ID_RE = re.compile(r"^act-[0-9a-f-]{36}$")
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")
CURRENT_STATES = frozenset(
    {ActionState.PREPARING, ActionState.AWAITING_LOCAL_CONFIRMATION, ActionState.APPROVED}
)
TERMINAL_STATES = frozenset(
    {
        ActionState.REJECTED,
        ActionState.COMPLETED,
        ActionState.FAILED,
        ActionState.REFUSED,
        ActionState.RECOVERY_REQUIRED,
    }
)


class ActionStateError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class ActionRecord:
    action_id: str
    fingerprint: str
    state: ActionState
    code: str
    updated_at: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_id": self.action_id,
            "fingerprint": self.fingerprint,
            "state": self.state.value,
            "code": self.code,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionRecord":
        if not isinstance(value, Mapping) or set(value) != RECORD_FIELDS:
            raise ActionStateError("Invalid action record fields")
        try:
            state = ActionState(value.get("state"))
        except (TypeError, ValueError) as exc:
            raise ActionStateError("Invalid action state") from exc
        action_id = value.get("action_id")
        fingerprint = value.get("fingerprint")
        code = value.get("code")
        updated_at = value.get("updated_at")
        if not isinstance(action_id, str) or ACTION_ID_RE.fullmatch(action_id) is None:
            raise ActionStateError("Invalid persisted action identifier")
        try:
            if uuid.UUID(action_id[4:]).version != 4:
                raise ActionStateError("Invalid persisted action identifier")
        except ValueError as exc:
            raise ActionStateError("Invalid persisted action identifier") from exc
        if not isinstance(fingerprint, str) or FINGERPRINT_RE.fullmatch(fingerprint) is None:
            raise ActionStateError("Invalid persisted fingerprint")
        if not isinstance(code, str) or not code or len(code) > 64:
            raise ActionStateError("Invalid persisted action code")
        if type(updated_at) not in (int, float) or updated_at < 0:
            raise ActionStateError("Invalid persisted action timestamp")
        return cls(action_id, fingerprint, state, code, float(updated_at))


@dataclass(frozen=True, slots=True)
class ActionStateSnapshot:
    current: ActionRecord | None
    terminal: tuple[ActionRecord, ...]
    state_version: int = ACTION_STATE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_version": self.state_version,
            "current": None if self.current is None else self.current.to_dict(),
            "terminal": [record.to_dict() for record in self.terminal],
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActionStateSnapshot":
        if not isinstance(value, Mapping) or set(value) != SNAPSHOT_FIELDS:
            raise ActionStateError("Invalid action snapshot fields")
        if value.get("state_version") != ACTION_STATE_VERSION:
            raise ActionStateError("Unsupported action state version")
        raw_current = value.get("current")
        raw_terminal = value.get("terminal")
        if raw_current is not None and not isinstance(raw_current, Mapping):
            raise ActionStateError("Invalid current action")
        if not isinstance(raw_terminal, list) or len(raw_terminal) > MAX_TERMINAL_ACTIONS:
            raise ActionStateError("Invalid terminal actions")
        current = None if raw_current is None else ActionRecord.from_dict(raw_current)
        terminal = tuple(ActionRecord.from_dict(item) for item in raw_terminal)
        if current is not None and current.state not in CURRENT_STATES:
            raise ActionStateError("Current action is terminal")
        if any(record.state not in TERMINAL_STATES for record in terminal):
            raise ActionStateError("Terminal action is not terminal")
        identifiers = [record.action_id for record in terminal]
        if len(set(identifiers)) != len(identifiers):
            raise ActionStateError("Duplicate terminal action")
        if current is not None and current.action_id in identifiers:
            raise ActionStateError("Current action was already terminal")
        return cls(current, terminal)
