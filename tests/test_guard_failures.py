from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.store import InvalidSnapshot
from holon_guard_ipc import GuardState
from guard_support import ACTION_ID, FINGERPRINT, make_ledger


class NoWallet:
    def open_or_activate(self, flow_id: str) -> object:
        del flow_id
        raise AssertionError("Wallet must not start")

    def request_close(self, handle: object) -> None:
        del handle


class AliveOwner:
    def is_alive(self, pid: int) -> bool:
        del pid
        return True


class GuardFailureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "state.json"
        self.store = SnapshotStore(self.path)

    def test_unreadable_snapshot_is_invalid(self) -> None:
        with patch.object(Path, "read_bytes", side_effect=PermissionError("private")):
            with self.assertRaises(InvalidSnapshot):
                self.store.load()

    def test_failed_atomic_replace_preserves_previous_snapshot(self) -> None:
        previous = self.store.bootstrap_normal_for_test(1.0)
        replacement = type(previous)(
            GuardState.SIGNING_DISABLED, None, None, None, "TEST", 2.0
        )
        with patch("holon_guard.store.os.replace", side_effect=OSError("disk failure")):
            with self.assertRaises(OSError):
                self.store.save(replacement)
        self.assertEqual(self.store.load(), previous)
        self.assertFalse(list(self.path.parent.glob(".guard-state-*.tmp")))

    def test_persistence_failure_disables_signing_before_wallet_launch(self) -> None:
        snapshot = self.store.bootstrap_normal_for_test(1.0)
        ledger = make_ledger(Path(self.temporary.name))
        guard = GuardLifecycle(self.store, snapshot, NoWallet(), AliveOwner(), ledger)
        with patch.object(self.store, "save", side_effect=OSError("disk failure")):
            result = guard.start_flow(101, ACTION_ID, FINGERPRINT)
        self.assertEqual(result.code, "SIGNING_DISABLED")
        self.assertIs(result.state, GuardState.SIGNING_DISABLED)

    def test_action_state_write_failure_disables_signing_before_wallet_launch(self) -> None:
        snapshot = self.store.bootstrap_normal_for_test(1.0)
        ledger = make_ledger(Path(self.temporary.name))
        guard = GuardLifecycle(self.store, snapshot, NoWallet(), AliveOwner(), ledger)
        with patch.object(ledger.store, "save", side_effect=OSError("disk failure")):
            result = guard.start_flow(101, ACTION_ID, FINGERPRINT)
        self.assertEqual(result.code, "SIGNING_DISABLED")
        self.assertIs(result.state, GuardState.SIGNING_DISABLED)


if __name__ == "__main__":
    unittest.main()
