from __future__ import annotations

import json

import pytest

from holon_wallet_control.lending_operation import (
    LendingOperation, LendingOperationSnapshot, LendingOperationStateError,
    LendingOperationStore,
)


def operation(**changes) -> LendingOperation:
    value = {
        "operation_id": "act-11111111-1111-4111-8111-111111111111",
        "requested_action": "supply", "amount_mode": "all", "amount": None,
        "resolved_amount_atomic": 2_000_000, "owner_pid": 42,
        "policy_version": "3", "policy_revision": 7,
        "policy_digest": "1" * 64, "action_profile_digest": "2" * 64,
        "safety_digest": "3" * 64, "phase": "approve_receipt",
        "phase_action_id": "act-22222222-2222-4222-8222-222222222222",
        "phase_fingerprint": "4" * 64, "created_at": "2026-07-26T00:00:00Z",
        "account_profile_id": "profile-1",
        "account_address": "0x1111111111111111111111111111111111111111",
        "transaction_hash": "0x" + "5" * 64, "receipt_state": "unknown",
        "updated_at": "2026-07-26T00:01:00Z",
    }
    value.update(changes)
    return LendingOperation(**value)


def test_operation_state_is_atomic_strict_and_bounded(tmp_path) -> None:
    store = LendingOperationStore(tmp_path / "lending-operation-state.json")
    current = operation()
    store.save(LendingOperationSnapshot(current))
    assert store.load().current == current
    raw = store.path.read_text(encoding="utf-8")
    for forbidden in ("password", "private_key", "calldata", "signed"):
        assert forbidden not in raw.lower()

    corrupted = json.loads(raw)
    corrupted["current"]["unexpected"] = True
    store.path.write_text(json.dumps(corrupted), encoding="utf-8")
    with pytest.raises(LendingOperationStateError):
        store.load()


def test_resume_requires_confirmed_receipt_and_fresh_owner() -> None:
    with pytest.raises(LendingOperationStateError):
        operation(phase="resume_or_revoke").resume(50)
    confirmed = operation(
        phase="resume_or_revoke", receipt_state="confirmed",
    ).resume(50)
    assert confirmed.phase == "prepare_supply"
    assert confirmed.owner_pid == 50


def test_receipt_mutation_and_terminal_history_fail_closed(tmp_path) -> None:
    value = operation().with_receipt(
        "0x" + "6" * 64, "confirmed", "2026-07-26T00:02:00Z",
    )
    assert value.transaction_hash == "0x" + "6" * 64
    store = LendingOperationStore(tmp_path / "lending-operation-state.json")
    with pytest.raises(LendingOperationStateError):
        store.save(LendingOperationSnapshot(None, (value,)))
