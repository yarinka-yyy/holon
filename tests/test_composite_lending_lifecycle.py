from __future__ import annotations

from pathlib import Path

from holon_guard import GuardLifecycle, SnapshotStore
from holon_wallet_control.lending_operation import (
    LendingOperationSnapshot,
    LendingOperationStore,
)
from holon_guard.wallet import WalletPreparedResult
from holon_guard_ipc import GuardState
from holon_lending import AAVE_SAFETY_DIGEST

from guard_support import ACTION_ID, FINGERPRINT, make_ledger


class Handle:
    pid = 202

    def poll(self):
        return None


class Owner:
    def is_alive(self, pid):
        return pid > 0


class CompositeWallet:
    def __init__(self):
        self.handle = Handle()
        self.requests = []

    def prepare_lending_action(self, request):
        self.requests.append(request)
        method = "approve" if len(self.requests) == 1 else "supply"
        return WalletPreparedResult(True, "TRANSFER_PREPARED", {
            "prepared_digest": ("a" if method == "approve" else "b") * 64,
            "max_total_fee_wei": "50000", "amount_atomic": "2000000",
            "next_action": method, "profile_id": "profile-1",
            "sender": "0x1111111111111111111111111111111111111111",
            "network": "base", "asset": "usdc",
            "target": "0x3333333333333333333333333333333333333333",
            "selector": "0x095ea7b3" if method == "approve" else "0x617ba037",
            "calldata_hash": ("c" if method == "approve" else "d") * 64,
        }, self.handle)

    def cancel_transfer(self, request):
        return bool(request)


def status(guard, event, code, *, outcome=None, receipt_state, tx_hash):
    operation = guard.lending_operation_snapshot.current
    assert operation is not None
    return {
        "flow_id": guard.snapshot.flow_id,
        "action_id": guard.snapshot.action_id,
        "prepared_digest": guard.prepared_digest,
        "wallet_pid": 202, "event": event, "code": code, "outcome": outcome,
        "operation_id": operation.operation_id,
        "phase_action_id": operation.phase_action_id,
        "phase": "approve" if operation.phase.startswith("approve") else "supply",
        "transaction_hash": tx_hash, "receipt_state": receipt_state,
    }


def test_approve_receipt_creates_fresh_supply_phase_without_auto_signing(tmp_path: Path) -> None:
    state_store = SnapshotStore(tmp_path / "guard-state.json")
    state_store.bootstrap_normal_for_test(1.0)
    operation_store = LendingOperationStore(tmp_path / "lending-operation-state.json")
    wallet = CompositeWallet()
    guard = GuardLifecycle(
        state_store, state_store.load(), wallet, Owner(), make_ledger(tmp_path),
        operation_store, operation_store.load(), clock=lambda: 2.0,
    )
    result, prepared = guard.start_lending_intent(
        101, ACTION_ID, FINGERPRINT,
        {"action": "supply", "amount_mode": "exact", "amount": "2"},
        2_000_000, "3", None, None, 7,
        "1" * 64, "2" * 64, operation_id=ACTION_ID,
    )
    assert result.ok and prepared["next_action"] == "approve"
    assert guard.lending_operation_snapshot.current.phase == "approve_review"

    tx_hash = "0x" + "3" * 64
    assert guard.accept_wallet_status(status(
        guard, "BROADCASTED", "PENDING", outcome="pending",
        receipt_state="pending", tx_hash=tx_hash,
    ))
    assert guard.snapshot.state is GuardState.ACTIVE
    assert guard.lending_operation_snapshot.current.phase == "approve_receipt"
    assert guard.accept_wallet_status(status(
        guard, "RECEIPT_CONFIRMED", "CONFIRMED", receipt_state="confirmed",
        tx_hash=tx_hash,
    ))
    assert guard.snapshot.state is GuardState.NORMAL
    assert guard.lending_operation_snapshot.current.phase == "prepare_supply"

    advanced = guard.advance_lending_operation()
    assert advanced is not None and advanced.ok
    operation = guard.lending_operation_snapshot.current
    assert operation.phase == "supply_review"
    assert operation.phase_action_id != ACTION_ID
    assert wallet.requests[1]["operation_id"] == ACTION_ID
    assert wallet.requests[1]["phase"] == "supply"
    assert wallet.requests[1]["resolved_amount_atomic"] == "2000000"
    assert operation.safety_digest == AAVE_SAFETY_DIGEST
    assert guard.ledger.snapshot.current is not None

    assert guard.accept_wallet_status(status(
        guard, "REJECTED", "ACTION_EDIT_REQUESTED",
        receipt_state="none", tx_hash=None,
    ))
    assert guard.snapshot.state is GuardState.NORMAL
    recovery = guard.lending_operation_snapshot.current
    assert recovery is not None and recovery.phase == "resume_or_revoke"

    operation_store.save(LendingOperationSnapshot(
        recovery.with_phase("supply_review"),
        guard.lending_operation_snapshot.terminal,
    ))
    restored = GuardLifecycle.restore(
        state_store, wallet, Owner(), guard.ledger,
        operation_store, operation_store.load(),
    )
    assert restored.lending_operation_snapshot.current.phase == "resume_or_revoke"


def test_failed_approve_review_clears_current_lending_operation(tmp_path: Path) -> None:
    state_store = SnapshotStore(tmp_path / "guard-state.json")
    state_store.bootstrap_normal_for_test(1.0)
    operation_store = LendingOperationStore(tmp_path / "lending-operation-state.json")
    guard = GuardLifecycle(
        state_store, state_store.load(), CompositeWallet(), Owner(),
        make_ledger(tmp_path), operation_store, operation_store.load(),
        clock=lambda: 2.0,
    )
    result, _prepared = guard.start_lending_intent(
        101, ACTION_ID, FINGERPRINT,
        {"action": "supply", "amount_mode": "exact", "amount": "2"},
        2_000_000, "3", None, None, 7,
        "1" * 64, "2" * 64, operation_id=ACTION_ID,
    )
    assert result.ok

    assert guard.accept_wallet_status(status(
        guard, "FAILED", "REVALIDATION_FAILED",
        receipt_state="none", tx_hash=None,
    ))
    assert guard.snapshot.state is GuardState.NORMAL
    assert guard.lending_operation_snapshot.current is None
    assert guard.lending_operation_snapshot.terminal[-1].phase == "failed"
