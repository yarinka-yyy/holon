from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.lock import GuardAlreadyRunning, SingleInstanceLock
from holon_guard.model import GuardSnapshot
from holon_guard.store import InvalidSnapshot
from holon_guard.wallet import UnavailableWalletController
from holon_guard_ipc import GuardState
from guard_support import ACTION_ID, FINGERPRINT, make_ledger


class AliveOwner:
    def is_alive(self, pid: int) -> bool:
        del pid
        return True


class GuardStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.path = Path(self.temporary.name) / "guard-state.json"
        self.store = SnapshotStore(self.path)

    def restore(self, ledger=None) -> GuardLifecycle:
        return GuardLifecycle.restore(
            self.store, UnavailableWalletController(), AliveOwner(),
            ledger or make_ledger(self.path.parent),
        )

    def test_missing_and_corrupt_snapshots_fail_closed(self) -> None:
        self.assertIs(self.restore().snapshot.state, GuardState.SIGNING_DISABLED)
        self.path.write_text("{broken", encoding="utf-8")
        self.assertIs(self.restore().snapshot.state, GuardState.SIGNING_DISABLED)

    def test_restart_never_resumes_protected_states(self) -> None:
        for state in (GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING):
            wallet_pid = None if state is GuardState.ENTERING else 202
            snapshot = GuardSnapshot(
                state, "flow", 101, wallet_pid, "TEST", 1.0, ACTION_ID, FINGERPRINT
            )
            self.store.save(snapshot)
            ledger = make_ledger(self.path.parent)
            ledger.begin(ACTION_ID, FINGERPRINT)
            restored = self.restore(ledger)
            self.assertIs(restored.snapshot.state, GuardState.RECOVERY_REQUIRED)
            self.assertEqual(restored.snapshot.flow_id, "flow")
            restarted_again = self.restore(ledger)
            self.assertIs(restarted_again.snapshot.state, GuardState.RECOVERY_REQUIRED)

    def test_normal_and_recovery_snapshots_are_preserved(self) -> None:
        normal = self.store.bootstrap_normal_for_test(0.0)
        self.assertEqual(self.restore().snapshot, normal)
        recovery = GuardSnapshot(
            GuardState.RECOVERY_REQUIRED, "flow", None, None, "TEST", 1.0,
            ACTION_ID, FINGERPRINT,
        )
        self.store.save(recovery)
        ledger = make_ledger(self.path.parent)
        ledger.begin(ACTION_ID, FINGERPRINT)
        self.assertEqual(self.restore(ledger).snapshot, recovery)

    def test_atomic_snapshot_is_strict_and_secret_free(self) -> None:
        self.store.bootstrap_normal_for_test(1.0)
        value = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(
            set(value),
            {
                "state_version", "state", "flow_id", "owner_pid", "wallet_pid",
                "reason", "updated_at", "action_id", "action_fingerprint",
            },
        )
        self.assertFalse(list(self.path.parent.glob(".guard-state-*.tmp")))
        value["secret"] = "forbidden"
        self.path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(InvalidSnapshot):
            self.store.load()

    def test_impossible_snapshot_is_rejected(self) -> None:
        value = self.store.bootstrap_normal_for_test().to_dict()
        value.update({"state": "ACTIVE", "flow_id": None, "owner_pid": None})
        self.path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(InvalidSnapshot):
            self.store.load()

    def test_legacy_state_version_is_not_migrated(self) -> None:
        value = self.store.bootstrap_normal_for_test().to_dict()
        value["state_version"] = 1
        self.path.write_text(json.dumps(value), encoding="utf-8")
        self.assertIs(self.restore().snapshot.state, GuardState.SIGNING_DISABLED)

    def test_single_instance_lock_releases_cleanly(self) -> None:
        path = self.path.parent / "guard.lock"
        first = SingleInstanceLock(path)
        second = SingleInstanceLock(path)
        first.acquire()
        with self.assertRaises(GuardAlreadyRunning):
            second.acquire()
        first.release()
        second.acquire()
        second.release()


if __name__ == "__main__":
    unittest.main()
