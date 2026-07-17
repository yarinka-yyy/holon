from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from holon_contracts import ActionState, MessageKind, make_envelope
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.authority import AuthorityService
from holon_journal import EventType, JournalFailure
from guard_support import enabled_policy, make_audit, make_ledger, transfer_request


class Handle:
    pid = 202

    def __init__(self) -> None:
        self.exit_code = None

    def poll(self):
        return self.exit_code


class Wallet:
    def __init__(self) -> None:
        self.calls = 0
        self.closed = 0

    def open_or_activate(self, flow_id: str) -> Handle:
        del flow_id
        self.calls += 1
        return Handle()

    def request_close(self, handle: Handle) -> None:
        self.closed += 1
        handle.exit_code = 0


class Owner:
    def is_alive(self, pid: int) -> bool:
        return pid > 0


class FailingJournal:
    def emit(self, *args, **kwargs):
        del args, kwargs
        raise JournalFailure("JOURNAL_WRITE_FAILED")


class M204IntegrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        store = SnapshotStore(root / "guard-state.json")
        store.bootstrap_normal_for_test(1.0)
        self.wallet = Wallet()
        self.audit = make_audit(root)
        lifecycle = GuardLifecycle(store, store.load(), self.wallet, Owner(), make_ledger(root))
        self.service = AuthorityService(lifecycle, enabled_policy(), self.audit)

    def test_third_equivalent_request_blocks_and_recovers_active_flow(self) -> None:
        first = self.service.handle(transfer_request(), 101)
        second = self.service.handle(transfer_request("act-44444444-4444-4444-8444-444444444444"), 101)
        third = self.service.handle(transfer_request("act-55555555-5555-4555-8555-555555555555"), 101)
        self.assertEqual(first.kind, MessageKind.PROTECTED_FLOW_STARTED)
        self.assertEqual(second.payload["code"], "ACTION_ALREADY_ACTIVE")
        self.assertEqual(third.payload["code"], "REQUEST_TEMPORARILY_BLOCKED")
        self.assertEqual(self.wallet.calls, 1)
        self.assertEqual(self.wallet.closed, 1)
        self.assertEqual(self.service.lifecycle.snapshot.state.value, "RECOVERY_REQUIRED")
        states = {record.action_id: record.state for record in self.service.lifecycle.ledger.snapshot.terminal}
        self.assertEqual(states[second.action_id], ActionState.REFUSED)
        self.assertEqual(states[third.action_id], ActionState.REFUSED)
        self.assertEqual(states[first.action_id], ActionState.RECOVERY_REQUIRED)
        event_types = {event.event_type for event in self.audit.journal.events()}
        self.assertIn(EventType.REQUEST_BLOCK_STARTED, event_types)
        health = self.service.handle(make_envelope(MessageKind.HEALTH_REQUEST, {}), None)
        self.assertEqual(health.payload["guard_state"], "RECOVERY_REQUIRED")
        recovery = make_envelope(MessageKind.RECOVER_ACTION, {}, action_id=first.action_id)
        self.assertEqual(self.service.handle(recovery, None).payload["guard_state"], "NORMAL")

    def test_journal_failure_closes_active_flow_and_fails_closed(self) -> None:
        started = self.service.handle(transfer_request(), 101)
        self.audit.journal = FailingJournal()
        failed = self.service.handle(
            transfer_request("act-66666666-6666-4666-8666-666666666666"), 101
        )
        self.assertEqual(started.kind, MessageKind.PROTECTED_FLOW_STARTED)
        self.assertEqual(failed.payload["code"], "JOURNAL_WRITE_FAILED")
        self.assertEqual(self.service.lifecycle.snapshot.state.value, "RECOVERY_REQUIRED")
        health = self.service.handle(make_envelope(MessageKind.HEALTH_REQUEST, {}), None)
        self.assertEqual(health.payload["code"], "JOURNAL_WRITE_FAILED")

    def test_request_state_write_failure_closes_active_flow(self) -> None:
        self.service.handle(transfer_request(), 101)
        with patch.object(self.audit.requests.store, "save", side_effect=OSError("canary")):
            failed = self.service.handle(
                transfer_request("act-77777777-7777-4777-8777-777777777777"), 101
            )
        self.assertEqual(failed.payload["code"], "REQUEST_CONTROL_STATE_INVALID")
        self.assertEqual(self.wallet.calls, 1)
        self.assertEqual(self.wallet.closed, 1)
        self.assertEqual(self.service.lifecycle.snapshot.state.value, "RECOVERY_REQUIRED")


if __name__ == "__main__":
    unittest.main()
