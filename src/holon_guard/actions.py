"""Fail-closed action lifecycle and persistent replay protection."""

from __future__ import annotations

import time
from typing import Callable

from holon_contracts import ActionState, RefusalCode, SecurityCode

from .action_model import MAX_TERMINAL_ACTIONS, ActionRecord, ActionStateSnapshot
from .action_store import ActionStateStore

ALLOWED_TRANSITIONS = {
    ActionState.PREPARING: frozenset({ActionState.AWAITING_LOCAL_CONFIRMATION}),
    ActionState.AWAITING_LOCAL_CONFIRMATION: frozenset({ActionState.APPROVED}),
}
ALLOWED_TERMINALS = {
    ActionState.PREPARING: frozenset({ActionState.FAILED, ActionState.RECOVERY_REQUIRED}),
    ActionState.AWAITING_LOCAL_CONFIRMATION: frozenset(
        {ActionState.REJECTED, ActionState.FAILED, ActionState.RECOVERY_REQUIRED}
    ),
    ActionState.APPROVED: frozenset(
        {ActionState.COMPLETED, ActionState.FAILED, ActionState.RECOVERY_REQUIRED}
    ),
}


class ActionLedgerFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Action state is unavailable")
        self.code = code


class ActionLedger:
    def __init__(
        self,
        store: ActionStateStore,
        snapshot: ActionStateSnapshot,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.snapshot = snapshot
        self.clock = clock

    def _save(self, snapshot: ActionStateSnapshot) -> None:
        try:
            self.store.save(snapshot)
        except OSError as exc:
            raise ActionLedgerFailure(SecurityCode.ACTION_STATE_INVALID.value) from exc
        self.snapshot = snapshot

    def find(self, action_id: str) -> ActionRecord | None:
        if self.snapshot.current is not None and self.snapshot.current.action_id == action_id:
            return self.snapshot.current
        return next(
            (record for record in self.snapshot.terminal if record.action_id == action_id), None
        )

    def preflight(self, action_id: str, fingerprint: str) -> None:
        self.check_identity(action_id, fingerprint)
        if self.snapshot.current is not None:
            raise ActionLedgerFailure(RefusalCode.ACTION_ALREADY_ACTIVE.value)
        if len(self.snapshot.terminal) >= MAX_TERMINAL_ACTIONS:
            raise ActionLedgerFailure(SecurityCode.ACTION_STATE_INVALID.value)

    def check_identity(self, action_id: str, fingerprint: str) -> None:
        existing = self.find(action_id)
        if existing is not None:
            code = (
                RefusalCode.ACTION_MUTATED.value
                if existing.fingerprint != fingerprint
                else RefusalCode.ACTION_REPLAYED.value
            )
            raise ActionLedgerFailure(code)

    def refuse(self, action_id: str, fingerprint: str, code: str) -> ActionRecord:
        self.check_identity(action_id, fingerprint)
        if len(self.snapshot.terminal) >= MAX_TERMINAL_ACTIONS:
            raise ActionLedgerFailure(SecurityCode.ACTION_STATE_INVALID.value)
        record = ActionRecord(action_id, fingerprint, ActionState.REFUSED, code, self.clock())
        self._save(
            ActionStateSnapshot(self.snapshot.current, self.snapshot.terminal + (record,))
        )
        return record

    def begin(self, action_id: str, fingerprint: str) -> ActionRecord:
        self.preflight(action_id, fingerprint)
        record = ActionRecord(
            action_id, fingerprint, ActionState.PREPARING, "ACTION_PREPARING", self.clock()
        )
        self._save(ActionStateSnapshot(record, self.snapshot.terminal))
        return record

    def transition(self, state: ActionState, code: str) -> ActionRecord:
        current = self.snapshot.current
        if current is None or state not in ALLOWED_TRANSITIONS.get(current.state, ()):
            raise ActionLedgerFailure(SecurityCode.ACTION_STATE_INVALID.value)
        record = ActionRecord(
            current.action_id, current.fingerprint, state, code, self.clock()
        )
        self._save(ActionStateSnapshot(record, self.snapshot.terminal))
        return record

    def terminalize(self, state: ActionState, code: str) -> ActionRecord:
        current = self.snapshot.current
        if current is None or state not in ALLOWED_TERMINALS.get(current.state, ()):
            raise ActionLedgerFailure(SecurityCode.ACTION_STATE_INVALID.value)
        record = ActionRecord(
            current.action_id, current.fingerprint, state, code, self.clock()
        )
        terminal = self.snapshot.terminal + (record,)
        if len(terminal) > MAX_TERMINAL_ACTIONS:
            raise ActionLedgerFailure(SecurityCode.ACTION_STATE_INVALID.value)
        self._save(ActionStateSnapshot(None, terminal))
        return record
