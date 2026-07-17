from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from holon_contracts import ActionState, RefusalCode, SecurityCode
from holon_guard.action_model import MAX_TERMINAL_ACTIONS, ActionRecord, ActionStateSnapshot
from holon_guard.action_store import (
    ActionStateStore, InvalidActionState, MissingActionState,
)
from holon_guard.actions import ActionLedger, ActionLedgerFailure

ACTION_ID = "act-22222222-2222-4222-8222-222222222222"
FINGERPRINT = "a" * 64


class ActionLedgerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = ActionStateStore(Path(self.temporary.name) / "action-state.json")
        snapshot = self.store.bootstrap_empty_for_test()
        self.ledger = ActionLedger(self.store, snapshot, clock=lambda: 1.0)

    def assert_failure(self, code: str, callback) -> None:
        with self.assertRaises(ActionLedgerFailure) as raised:
            callback()
        self.assertEqual(raised.exception.code, code)

    def test_current_transition_terminal_and_restart_replay(self) -> None:
        self.ledger.begin(ACTION_ID, FINGERPRINT)
        current = self.ledger.transition(
            ActionState.AWAITING_LOCAL_CONFIRMATION, "AWAITING_LOCAL_CONFIRMATION"
        )
        self.assertEqual(current.state, ActionState.AWAITING_LOCAL_CONFIRMATION)
        terminal = self.ledger.terminalize(ActionState.REJECTED, "ACTION_CANCELLED")
        self.assertEqual(terminal.state, ActionState.REJECTED)
        restarted = ActionLedger(self.store, self.store.load())
        self.assertEqual(restarted.find(ACTION_ID), terminal)
        self.assert_failure(
            RefusalCode.ACTION_REPLAYED.value,
            lambda: restarted.begin(ACTION_ID, FINGERPRINT),
        )
        self.assert_failure(
            RefusalCode.ACTION_MUTATED.value,
            lambda: restarted.begin(ACTION_ID, "b" * 64),
        )

    def test_one_active_action_and_atomic_write_failure(self) -> None:
        self.ledger.begin(ACTION_ID, FINGERPRINT)
        self.assert_failure(
            SecurityCode.ACTION_STATE_INVALID.value,
            lambda: self.ledger.transition(ActionState.COMPLETED, "COMPLETED"),
        )
        self.assert_failure(
            SecurityCode.ACTION_STATE_INVALID.value,
            lambda: self.ledger.terminalize(ActionState.COMPLETED, "COMPLETED"),
        )
        other = f"act-{uuid.uuid4()}"
        self.assert_failure(
            RefusalCode.ACTION_ALREADY_ACTIVE.value,
            lambda: self.ledger.begin(other, "b" * 64),
        )
        with patch.object(self.store, "save", side_effect=OSError("disk")):
            self.assert_failure(
                SecurityCode.ACTION_STATE_INVALID.value,
                lambda: self.ledger.transition(ActionState.APPROVED, "APPROVED"),
            )

    def test_missing_and_corrupt_state_are_not_bootstrapped_implicitly(self) -> None:
        missing = ActionStateStore(Path(self.temporary.name) / "missing.json")
        with self.assertRaises(MissingActionState):
            missing.load()
        self.store.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(InvalidActionState):
            self.store.load()

    def test_capacity_disables_new_actions_without_eviction(self) -> None:
        terminal = tuple(
            ActionRecord(
                f"act-{uuid.uuid4()}", f"{index:064x}", ActionState.REJECTED,
                "ACTION_CANCELLED", 1.0,
            )
            for index in range(MAX_TERMINAL_ACTIONS)
        )
        ledger = ActionLedger(self.store, ActionStateSnapshot(None, terminal))
        self.assert_failure(
            SecurityCode.ACTION_STATE_INVALID.value,
            lambda: ledger.begin(ACTION_ID, FINGERPRINT),
        )


if __name__ == "__main__":
    unittest.main()
