from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from holon_contracts import MessageKind, make_envelope
from holon_lending import ACTION_PROFILES_DIGEST, ActionProfilesState
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.authority import AuthorityService
from holon_guard.wallet import (
    WalletBalancesResult, WalletLendingPreviewResult, WalletOpenResult,
    WalletPreparedResult,
)
from holon_policy import LendingRule, Policy, PolicyEngine, PolicySnapshot, policy_digest
from guard_support import (
    ACTION_ID, ACTION_ID_2, enabled_policy, make_audit, make_ledger, transfer_request,
)


class Handle:
    pid = 202

    def __init__(self) -> None:
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


class Wallet:
    def __init__(self) -> None:
        self.calls = 0
        self.open_calls = 0
        self.balance_calls = 0
        self.preview_calls = 0
        self.preview_payload = None
        self.lending_prepare_calls = 0
        self.handle = Handle()

    def open_or_activate(self, flow_id: str) -> Handle:
        del flow_id
        self.calls += 1
        return self.handle

    def request_close(self, handle: Handle) -> None:
        handle.exit_code = 0

    def open_public(self) -> WalletOpenResult:
        self.open_calls += 1
        return WalletOpenResult(
            True, "ACTIVATED", "WALLET_ACTIVATED", "Wallet is open.",
        )

    def read_public_balances(self) -> WalletBalancesResult:
        self.balance_calls += 1
        networks = [
            {
                "network": network, "chain_id": chain_id,
                "status": "UNAVAILABLE", "block_number": None,
                "updated_at": None, "error_code": "RPC_UNAVAILABLE",
                "balances": None,
            }
            for network, chain_id in (("ethereum", 1), ("base", 8453))
        ]
        return WalletBalancesResult(
            True,
            {
                "status": "DEGRADED", "authority_available": False,
                "account": {
                    "label": "Account 1",
                    "address": "0x1111111111111111111111111111111111111111",
                },
                "networks": networks, "code": "BALANCES_UNAVAILABLE",
                "message": "Wallet balances are unavailable.",
            },
        )

    def preview_lending(self, intent, profile_digest) -> WalletLendingPreviewResult:
        from holon_lending.preflight import unavailable_preview

        self.preview_calls += 1
        if self.preview_payload is not None:
            return WalletLendingPreviewResult(True, dict(self.preview_payload))
        return WalletLendingPreviewResult(
            True,
            unavailable_preview(
                "BASE_RPC_UNAVAILABLE", requested_action=intent["action"],
                amount_mode=intent["amount_mode"], profile_digest=profile_digest,
            ),
        )

    def prepare_lending_action(self, request) -> WalletPreparedResult:
        self.lending_prepare_calls += 1
        return WalletPreparedResult(True, "LENDING_ACTION_PREPARED", {
            "amount_atomic": "1000000",
            "max_total_fee_wei": "90000000000000",
            "prepared_digest": "a" * 64,
        }, self.handle)


class Owner:
    alive = True

    def is_alive(self, pid: int) -> bool:
        del pid
        return self.alive


class AuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        store = SnapshotStore(root / "guard-state.json")
        store.bootstrap_normal_for_test(1.0)
        self.wallet = Wallet()
        self.lifecycle = GuardLifecycle(
            store, store.load(), self.wallet, Owner(), make_ledger(root)
        )
        self.audit = make_audit(root)
        self.service = AuthorityService(self.lifecycle, enabled_policy(), self.audit)

    def test_policy_refusal_never_starts_wallet_or_action(self) -> None:
        refused = self.service.handle(transfer_request(network="ethereum"), owner_pid=101)
        self.assertEqual(refused.kind, MessageKind.REFUSAL)
        self.assertEqual(refused.payload["code"], "NETWORK_NOT_ALLOWED")
        self.assertEqual(self.wallet.calls, 0)
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)
        self.assertEqual(self.lifecycle.ledger.find(refused.action_id).state.value, "REFUSED")

    def test_prepare_status_mutation_and_one_active_action(self) -> None:
        started = self.service.handle(transfer_request(), owner_pid=101)
        self.assertEqual(started.kind, MessageKind.PROTECTED_FLOW_STARTED)
        status_request = replace(
            transfer_request(), kind=MessageKind.ACTION_STATUS_REQUEST, payload={}
        )
        status = self.service.handle(status_request, owner_pid=None)
        self.assertEqual(status.payload["action_state"], "AWAITING_LOCAL_CONFIRMATION")
        wrong_cancel = replace(
            status_request, kind=MessageKind.CANCEL_ACTION, action_id=ACTION_ID_2
        )
        self.assertEqual(
            self.service.handle(wrong_cancel, owner_pid=None).payload["code"],
            "ACTION_ID_INVALID",
        )
        mutated = self.service.handle(
            transfer_request(amount_atomic="999999"), owner_pid=101
        )
        self.assertEqual(mutated.payload["code"], "ACTION_MUTATED")
        other = self.service.handle(transfer_request(ACTION_ID_2), owner_pid=101)
        self.assertEqual(other.payload["code"], "ACTION_ALREADY_ACTIVE")

    def test_unexpected_clean_exit_requires_recovery_and_replay_stays_blocked(self) -> None:
        self.service.handle(transfer_request(), owner_pid=101)
        self.wallet.handle.exit_code = 0
        result = self.lifecycle.monitor_once()
        self.assertEqual(result.state.value, "RECOVERY_REQUIRED")
        request = replace(transfer_request(), kind=MessageKind.RECOVER_ACTION, payload={})
        recovered = self.service.handle(request, owner_pid=None)
        self.assertEqual(recovered.payload["guard_state"], "NORMAL")
        replay = self.service.handle(transfer_request(), owner_pid=101)
        self.assertEqual(replay.payload["code"], "ACTION_REPLAYED")

    def test_disabled_policy_is_a_refusal_not_a_technical_error(self) -> None:
        disabled = Policy("2", "1", False, ())
        service = AuthorityService(self.lifecycle, PolicyEngine(disabled), self.audit)
        response = service.handle(transfer_request(), owner_pid=101)
        self.assertEqual(response.kind, MessageKind.REFUSAL)
        self.assertEqual(response.payload["code"], "POLICY_AUTHORITY_DISABLED")

    def test_health_does_not_echo_untrusted_persisted_reason(self) -> None:
        self.lifecycle.disable_signing("PRIVATE_SECRET")
        request = make_envelope(MessageKind.HEALTH_REQUEST, {})
        response = self.service.handle(request, owner_pid=None)
        self.assertEqual(response.payload["code"], "SIGNING_DISABLED")
        self.assertNotIn("PRIVATE_SECRET", str(response.to_dict()))

    def test_public_open_preserves_guard_state_and_creates_no_action(self) -> None:
        request = make_envelope(MessageKind.OPEN_WALLET, {})
        opened = self.service.handle(request, owner_pid=None)
        self.assertEqual(opened.kind, MessageKind.WALLET_OPENED)
        self.assertEqual(opened.payload["wallet_state"], "ACTIVATED")
        self.assertFalse(opened.payload["authority_available"])
        self.assertEqual(self.lifecycle.snapshot.state.value, "NORMAL")
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)
        self.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        opened_disabled = self.service.handle(request, owner_pid=None)
        self.assertEqual(opened_disabled.kind, MessageKind.WALLET_OPENED)
        self.assertEqual(opened_disabled.payload["guard_state"], "SIGNING_DISABLED")
        self.assertEqual(self.wallet.open_calls, 2)

    def test_public_open_failure_is_generic_and_keeps_authority_untouched(self) -> None:
        def fail():
            raise RuntimeError("private path and process detail")

        self.wallet.open_public = fail  # type: ignore[method-assign]
        response = self.service.handle(make_envelope(MessageKind.OPEN_WALLET, {}), None)
        self.assertEqual(response.kind, MessageKind.ERROR)
        self.assertEqual(response.payload["code"], "WALLET_UNAVAILABLE")
        self.assertNotIn("private", str(response.to_dict()).lower())
        self.assertEqual(self.lifecycle.snapshot.state.value, "NORMAL")
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)

    def test_public_balances_preserve_guard_state_and_create_no_action(self) -> None:
        request = make_envelope(MessageKind.READ_WALLET_BALANCES, {})
        response = self.service.handle(request, owner_pid=None)
        self.assertEqual(response.kind, MessageKind.WALLET_BALANCES)
        self.assertEqual(response.payload["status"], "DEGRADED")
        self.assertFalse(response.payload["authority_available"])
        self.assertNotIn("action_id", response.to_dict())
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)
        self.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        disabled = self.service.handle(request, owner_pid=None)
        self.assertEqual(disabled.kind, MessageKind.WALLET_BALANCES)

    def test_public_balance_failure_is_generic(self) -> None:
        def fail():
            raise RuntimeError("private endpoint and query detail")

        self.wallet.read_public_balances = fail  # type: ignore[method-assign]
        response = self.service.handle(
            make_envelope(MessageKind.READ_WALLET_BALANCES, {}), None,
        )
        self.assertEqual(response.kind, MessageKind.ERROR)
        self.assertEqual(response.payload["code"], "WALLET_BALANCES_UNAVAILABLE")
        self.assertNotIn("private", str(response.to_dict()).lower())

    def test_lending_reads_are_public_and_work_when_signing_disabled(self) -> None:
        from holon_lending import LendingReadService

        self.service.lending = LendingReadService.unavailable()
        compare = self.service.handle(
            make_envelope(MessageKind.READ_LENDING_MARKETS, {}), None,
        )
        self.assertEqual(compare.kind, MessageKind.LENDING_MARKETS)
        self.assertEqual(compare.payload["code"], "LENDING_UNAVAILABLE")
        self.assertEqual(self.wallet.balance_calls, 0)

        self.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        positions = self.service.handle(
            make_envelope(MessageKind.READ_LENDING_POSITIONS, {}), None,
        )
        self.assertEqual(positions.kind, MessageKind.LENDING_POSITIONS)
        self.assertEqual(positions.payload["code"], "LENDING_POSITIONS_UNAVAILABLE")
        self.assertEqual(self.wallet.balance_calls, 1)
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)

    def test_lending_preview_works_when_signing_disabled_without_action_state(self) -> None:
        request = make_envelope(
            MessageKind.LENDING_ACTION_INTENT,
            {
                "module_id": "lending", "module_version": "1",
                "protocol_profile_id": "aave-v3-base-usdc",
                "protocol_profile_version": "1", "network": "base", "asset": "usdc",
                "beneficiary_mode": "active_wallet_account", "action": "supply",
                "amount_mode": "exact", "amount": "1",
            },
        )
        self.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        response = self.service.handle(request, owner_pid=None)
        self.assertEqual(response.kind, MessageKind.LENDING_ACTION_PREVIEW)
        self.assertEqual(response.payload["status"], "UNAVAILABLE")
        self.assertEqual(self.wallet.preview_calls, 1)
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)
        self.assertNotIn("action_id", response.to_dict())

    def test_lending_authority_starts_one_protected_action_under_v3_only(self) -> None:
        rule = LendingRule(
            "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
            ("approve", "supply"), "5000000", "100000000000000",
            ACTION_PROFILES_DIGEST,
        )
        policy = Policy("3", "2", False, (), True, (rule,))
        snapshot = PolicySnapshot(2, policy_digest(policy.to_dict()), policy, "d" * 64)
        service = AuthorityService(
            self.lifecycle, PolicyEngine(policy), self.audit,
            policy_snapshot=snapshot, lending_actions=ActionProfilesState.load(),
        )
        request = make_envelope(
            MessageKind.LENDING_AUTHORITY_INTENT,
            {
                "module_id": "lending", "module_version": "1",
                "protocol_profile_id": "aave-v3-base-usdc",
                "protocol_profile_version": "1", "network": "base", "asset": "usdc",
                "beneficiary_mode": "active_wallet_account", "action": "supply",
                "amount_mode": "exact", "amount": "1",
            }, action_id=ACTION_ID,
        )
        response = service.handle(request, owner_pid=101)
        self.assertEqual(response.kind, MessageKind.PROTECTED_FLOW_STARTED)
        self.assertEqual(self.wallet.lending_prepare_calls, 1)
        self.assertEqual(self.lifecycle.snapshot.state.value, "ACTIVE")
        self.assertEqual(
            service.handle(request, owner_pid=101).payload["code"], "ACTION_REPLAYED",
        )

    def test_withdraw_all_resolves_position_before_starting_protected_flow(self) -> None:
        rule = LendingRule(
            "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
            ("withdraw",), "1010000", "100000000000000",
            ACTION_PROFILES_DIGEST,
        )
        policy = Policy("3", "2", False, (), True, (rule,))
        snapshot = PolicySnapshot(3, policy_digest(policy.to_dict()), policy, "d" * 64)
        service = AuthorityService(
            self.lifecycle, PolicyEngine(policy), self.audit,
            policy_snapshot=snapshot, lending_actions=ActionProfilesState.load(),
        )
        self.wallet.preview_payload = {
            "status": "PREVIEW_READY", "requested_action": "withdraw",
            "amount_mode": "all", "amount_atomic": "999999",
        }
        request = make_envelope(
            MessageKind.LENDING_AUTHORITY_INTENT,
            {
                "module_id": "lending", "module_version": "1",
                "protocol_profile_id": "aave-v3-base-usdc",
                "protocol_profile_version": "1", "network": "base", "asset": "usdc",
                "beneficiary_mode": "active_wallet_account", "action": "withdraw",
                "amount_mode": "all", "amount": None,
            }, action_id=ACTION_ID,
        )
        response = service.handle(request, owner_pid=101)
        self.assertEqual(response.kind, MessageKind.PROTECTED_FLOW_STARTED)
        self.assertEqual(self.wallet.preview_calls, 1)
        self.assertEqual(self.wallet.lending_prepare_calls, 1)
        replay = service.handle(request, owner_pid=101)
        self.assertEqual(replay.payload["code"], "ACTION_REPLAYED")
        self.assertEqual(self.wallet.preview_calls, 1)


if __name__ == "__main__":
    unittest.main()
