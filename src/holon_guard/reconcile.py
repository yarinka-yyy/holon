"""Reconcile persisted Guard and replay state without resuming authority."""

from __future__ import annotations

from typing import Protocol

from holon_contracts import ActionState, SecurityCode
from holon_guard_ipc import GuardState

from .actions import ActionLedger, ActionLedgerFailure
from .model import GuardResult, GuardSnapshot


class ReconcileTarget(Protocol):
    snapshot: GuardSnapshot
    ledger: ActionLedger

    def disable_signing(self, reason: str) -> GuardResult: ...


def reconcile_action_state(guard: ReconcileTarget) -> None:
    current = guard.ledger.snapshot.current
    if guard.snapshot.state is GuardState.RECOVERY_REQUIRED:
        record = current or guard.ledger.find(guard.snapshot.action_id or "")
        if (
            record is None
            or record.action_id != guard.snapshot.action_id
            or record.fingerprint != guard.snapshot.action_fingerprint
        ):
            guard.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
            return
        if current is not None:
            try:
                guard.ledger.terminalize(ActionState.RECOVERY_REQUIRED, "GUARD_RESTARTED")
            except ActionLedgerFailure:
                guard.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
        elif record.state is not ActionState.RECOVERY_REQUIRED:
            guard.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
    elif guard.snapshot.state is GuardState.NORMAL and current is not None:
        guard.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
