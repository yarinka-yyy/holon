from __future__ import annotations

from holon_contracts import ActionState, SecurityCode
from holon_guard_ipc import GuardState

from .actions import ActionLedgerFailure
from .model import GuardResult, GuardSnapshot
from .startup import idle_snapshot

ACTIVE_STATES = frozenset({GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING})


def cancel_flow(guard, action_id: str) -> GuardResult:
    with guard._lock:
        if guard.snapshot.state not in ACTIVE_STATES:
            return guard._result(False, "FLOW_NOT_ACTIVE", "No cancellable flow is active.")
        if guard.snapshot.action_id != action_id:
            return guard._result(False, "ACTION_ID_MISMATCH", "Action identifier does not match.")
        if guard.wallet_handle is None:
            return guard._recover("WALLET_INTERRUPTED")
        exiting = GuardSnapshot(
            GuardState.EXITING, guard.snapshot.flow_id, guard.snapshot.owner_pid,
            guard.snapshot.wallet_pid, "FLOW_EXITING", guard.clock(),
            guard.snapshot.action_id, guard.snapshot.action_fingerprint,
        )
        if not guard._persist(exiting):
            return guard._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
        try:
            guard.wallet.request_close(guard.wallet_handle)
        except Exception:
            return guard._recover("WALLET_INTERRUPTED")
        return guard._result(True, "FLOW_EXITING", "Protected flow is closing.")


def recover_flow(guard, action_id: str) -> GuardResult:
    with guard._lock:
        if guard.snapshot.state is not GuardState.RECOVERY_REQUIRED:
            return guard._result(False, "RECOVERY_NOT_REQUIRED", "Recovery is not required.")
        if guard.snapshot.action_id != action_id:
            return guard._result(False, "ACTION_ID_MISMATCH", "Action identifier does not match.")
        if not guard._persist(idle_snapshot(GuardState.NORMAL, "RECOVERY_COMPLETED", guard.clock())):
            return guard._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
        return guard._result(True, "RECOVERY_COMPLETED", "Recovery completed; create a new flow.")


def interrupt_for_security_block(guard, code: str) -> GuardResult:
    with guard._lock:
        if guard.snapshot.state not in ACTIVE_STATES:
            return guard.health()
        if guard.wallet_handle is not None:
            try:
                if (
                    guard.prepared_digest is not None
                    and guard.snapshot.flow_id is not None
                    and guard.snapshot.action_id is not None
                ):
                    guard.wallet.cancel_transfer({
                        "authority_version": "1",
                        "kind": "cancel_transfer",
                        "flow_id": guard.snapshot.flow_id,
                        "action_id": guard.snapshot.action_id,
                        "prepared_digest": guard.prepared_digest,
                    })
                else:
                    guard.wallet.request_close(guard.wallet_handle)
            except Exception:
                pass
        return guard._recover(code)


def fail_started_action(guard, code: str) -> GuardResult:
    try:
        guard.ledger.terminalize(ActionState.FAILED, code)
    except ActionLedgerFailure:
        code = SecurityCode.ACTION_STATE_INVALID.value
    return guard.disable_signing(code)
