from __future__ import annotations

from pathlib import Path

from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.wallet import WalletPreparedResult
from holon_guard_ipc import GuardState
from guard_support import make_ledger

ACTION_ID = "act-22222222-2222-4222-8222-222222222222"


class Handle:
    pid = 202

    def poll(self):
        return None


class Owner:
    def is_alive(self, pid):
        return pid == 101


class Wallet:
    def __init__(self) -> None:
        self.requests = []
        self.cancel_requests = []

    def prepare_module_action(self, request):
        self.requests.append(dict(request))
        return WalletPreparedResult(True, "MODULE_ACTION_PREPARED", {
            "module_id": request["module_id"],
            "capability_id": request["capability_id"],
            "profile_id": request["profile_id"],
            "action_type": request["action_type"],
            "bundle_digest": request["bundle"]["bundle_digest"],
            "prepared_digest": "a" * 64,
        }, Handle())

    def cancel_transfer(self, request):
        self.cancel_requests.append(dict(request))
        return True


def bundle():
    return {
        "operation_id": ACTION_ID,
        "account": "0x" + "11" * 20,
        "action_type": "OPEN_POSITION",
        "bundle_digest": "b" * 64,
        "created_at": "2026-08-06T12:00:00.000Z",
        "expires_at": "2026-08-06T12:01:30.000Z",
    }


def lifecycle(tmp_path: Path):
    store = SnapshotStore(tmp_path / "guard-state.json")
    store.bootstrap_normal_for_test(1_786_000_000.0)
    wallet = Wallet()
    item = GuardLifecycle(
        store, store.load(), wallet, Owner(), make_ledger(tmp_path),
        clock=lambda: 1_786_000_000.0,
    )
    return item, wallet


def test_module_bundle_enters_wallet_review_and_partial_result_is_terminal(tmp_path: Path) -> None:
    item, wallet = lifecycle(tmp_path)
    result, prepared = item.start_module_intent(
        101, ACTION_ID, "f" * 64, "holon.perpdex",
        "holon.perpdex.action.wallet", "hyperliquid-mainnet-v1",
        "OPEN_POSITION", bundle(),
    )
    assert result.ok and result.state is GuardState.ACTIVE
    assert prepared["prepared_digest"] == "a" * 64
    assert wallet.requests[0]["kind"] == "prepare_module_action"
    assert item.prepared_audit_context == {
        "module_id": "holon.perpdex",
        "capability_id": "holon.perpdex.action.wallet",
        "action_type": "OPEN_POSITION",
        "wallet_address": "0x" + "11" * 20,
        "bundle_digest": "b" * 64,
        "local_approved_recorded": False,
    }

    accepted = item.accept_wallet_status({
        "flow_id": result.flow_id, "action_id": ACTION_ID,
        "prepared_digest": "a" * 64, "wallet_pid": 202,
        "event": "COMPLETED", "code": "IOC_PARTIAL_FILL",
    })
    assert accepted
    assert item.snapshot.state is GuardState.NORMAL
    assert item.ledger.find(ACTION_ID).state.value == "COMPLETED"


def test_module_cancel_uses_action_cancel_and_never_reuses_bundle(tmp_path: Path) -> None:
    item, wallet = lifecycle(tmp_path)
    result, _prepared = item.start_module_intent(
        101, ACTION_ID, "f" * 64, "holon.perpdex",
        "holon.perpdex.action.wallet", "hyperliquid-mainnet-v1",
        "OPEN_POSITION", bundle(),
    )
    assert result.ok
    cancelled = item.cancel_external_transfer(ACTION_ID)
    assert cancelled.ok
    assert wallet.cancel_requests == [{
        "authority_version": "2", "kind": "cancel_action",
        "flow_id": result.flow_id, "action_id": ACTION_ID,
        "prepared_digest": "a" * 64,
    }]
    assert item.ledger.find(ACTION_ID).state.value == "REJECTED"


def test_wallet_live_verify_refusal_keeps_its_safe_stage(tmp_path: Path) -> None:
    class RejectingWallet(Wallet):
        def prepare_module_action(self, request):
            self.requests.append(dict(request))
            return WalletPreparedResult(
                False, "HYPERLIQUID_UNAVAILABLE",
                {"stage": "WALLET_LIVE_VERIFY"}, Handle(),
            )

    store = SnapshotStore(tmp_path / "guard-state.json")
    store.bootstrap_normal_for_test(1_786_000_000.0)
    wallet = RejectingWallet()
    item = GuardLifecycle(
        store, store.load(), wallet, Owner(), make_ledger(tmp_path),
        clock=lambda: 1_786_000_000.0,
    )
    result, prepared = item.start_module_intent(
        101, ACTION_ID, "f" * 64, "holon.perpdex",
        "holon.perpdex.action.wallet", "hyperliquid-mainnet-v1",
        "OPEN_POSITION", bundle(),
    )
    assert not result.ok
    assert result.code == "HYPERLIQUID_UNAVAILABLE"
    assert result.stage == "WALLET_LIVE_VERIFY"
    assert prepared == {"stage": "WALLET_LIVE_VERIFY"}
    assert item.ledger.find(ACTION_ID).state.value == "FAILED"


def test_wallet_startup_timeout_uses_wallet_prepare_stage(tmp_path: Path) -> None:
    class TimeoutWallet(Wallet):
        def prepare_module_action(self, request):
            self.requests.append(dict(request))
            return WalletPreparedResult(False, "WALLET_STARTUP_TIMEOUT", None, None)

    store = SnapshotStore(tmp_path / "guard-state.json")
    store.bootstrap_normal_for_test(1_786_000_000.0)
    wallet = TimeoutWallet()
    item = GuardLifecycle(
        store, store.load(), wallet, Owner(), make_ledger(tmp_path),
        clock=lambda: 1_786_000_000.0,
    )
    result, prepared = item.start_module_intent(
        101, ACTION_ID, "f" * 64, "holon.perpdex",
        "holon.perpdex.action.wallet", "hyperliquid-mainnet-v1",
        "OPEN_POSITION", bundle(),
    )

    assert not result.ok
    assert result.code == "WALLET_STARTUP_TIMEOUT"
    assert result.stage == "WALLET_PREPARE"
    assert prepared is None
    assert item.ledger.find(ACTION_ID).state.value == "FAILED"


def test_wallet_protocol_ambiguity_keeps_safe_ipc_outcome_and_requires_recovery(
    tmp_path: Path,
) -> None:
    class AmbiguousWallet(Wallet):
        def prepare_module_action(self, request):
            self.requests.append(dict(request))
            return WalletPreparedResult(
                False, "WALLET_PREPARATION_AMBIGUOUS", {
                    "stage": "WALLET_PREPARE", "failure_category": "wallet_ipc",
                    "ipc_outcome": "WALLET_RESPONSE_SCHEMA_INVALID",
                }, None,
            )

    store = SnapshotStore(tmp_path / "guard-state.json")
    store.bootstrap_normal_for_test(1_786_000_000.0)
    wallet = AmbiguousWallet()
    item = GuardLifecycle(
        store, store.load(), wallet, Owner(), make_ledger(tmp_path),
        clock=lambda: 1_786_000_000.0,
    )
    result, prepared = item.start_module_intent(
        101, ACTION_ID, "f" * 64, "holon.perpdex",
        "holon.perpdex.action.wallet", "hyperliquid-mainnet-v1",
        "OPEN_POSITION", bundle(),
    )

    assert not result.ok and result.code == "WALLET_PREPARATION_AMBIGUOUS"
    assert result.stage == "WALLET_PREPARE"
    assert prepared["ipc_outcome"] == "WALLET_RESPONSE_SCHEMA_INVALID"
    assert item.snapshot.state is GuardState.RECOVERY_REQUIRED
    record = item.ledger.find(ACTION_ID)
    assert record.state.value == "RECOVERY_REQUIRED"
    assert record.code == "WALLET_PREPARATION_AMBIGUOUS"


def test_old_recovery_can_release_only_for_fresh_risk_reducing_exit(tmp_path: Path) -> None:
    item, _wallet = lifecycle(tmp_path)
    started, _prepared = item.start_module_intent(
        101, ACTION_ID, "f" * 64, "holon.perpdex",
        "holon.perpdex.action.wallet", "hyperliquid-mainnet-v1",
        "OPEN_POSITION", bundle(),
    )
    assert started.ok
    item._recover("WALLET_INTERRUPTED")
    refused = item.release_module_recovery_for_exit("OPEN_POSITION")
    assert not refused.ok and item.snapshot.state is GuardState.RECOVERY_REQUIRED
    released = item.release_module_recovery_for_exit("CLOSE_POSITION")
    assert released.ok and released.code == "RISK_REDUCING_EXIT_ALLOWED"
    assert item.snapshot.state is GuardState.NORMAL
