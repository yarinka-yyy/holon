from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from holon_contracts import MessageKind, make_envelope
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.authority import AuthorityService
from holon_policy import Policy, PolicyEngine
from guard_support import ACTION_ID, ACTION_ID_2, enabled_policy, make_ledger, transfer_request


class Handle:
    pid = 202

    def __init__(self) -> None:
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


class Wallet:
    def __init__(self) -> None:
        self.calls = 0
        self.handle = Handle()

    def open_or_activate(self, flow_id: str) -> Handle:
        del flow_id
        self.calls += 1
        return self.handle

    def request_close(self, handle: Handle) -> None:
        handle.exit_code = 0


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
        self.service = AuthorityService(self.lifecycle, enabled_policy())

    def test_policy_refusal_never_starts_wallet_or_action(self) -> None:
        refused = self.service.handle(
            transfer_request(network="ethereum"), owner_pid=101
        )
        self.assertEqual(refused.kind, MessageKind.REFUSAL)
        self.assertEqual(refused.payload["code"], "NETWORK_NOT_ALLOWED")
        self.assertEqual(self.wallet.calls, 0)
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)

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
        service = AuthorityService(self.lifecycle, PolicyEngine(disabled))
        response = service.handle(transfer_request(), owner_pid=101)
        self.assertEqual(response.kind, MessageKind.REFUSAL)
        self.assertEqual(response.payload["code"], "POLICY_AUTHORITY_DISABLED")

    def test_health_does_not_echo_untrusted_persisted_reason(self) -> None:
        self.lifecycle.disable_signing("PRIVATE_SECRET")
        request = make_envelope(MessageKind.HEALTH_REQUEST, {})
        response = self.service.handle(request, owner_pid=None)
        self.assertEqual(response.payload["code"], "SIGNING_DISABLED")
        self.assertNotIn("PRIVATE_SECRET", str(response.to_dict()))


if __name__ == "__main__":
    unittest.main()
