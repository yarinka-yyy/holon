from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from holon_contracts import MessageKind, make_envelope
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.authority import AuthorityService
from holon_guard.wallet import WalletBalancesResult, WalletOpenResult
from holon_policy import Policy, PolicyEngine
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
        disabled = Policy("1", "1", False, ())
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


if __name__ == "__main__":
    unittest.main()
