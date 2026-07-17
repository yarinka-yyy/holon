from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.model import GuardSnapshot
from holon_guard_ipc import GuardState


class FakeHandle:
    def __init__(self, pid: int = 202) -> None:
        self.pid = pid
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


class FakeWallet:
    def __init__(self, store: SnapshotStore) -> None:
        self.store = store
        self.handle = FakeHandle()
        self.opened_in: GuardState | None = None
        self.launch_error = False
        self.close_error = False

    def open_or_activate(self, flow_id: str) -> FakeHandle:
        del flow_id
        self.opened_in = self.store.load().state
        if self.launch_error:
            raise RuntimeError("mock launch failure")
        return self.handle

    def request_close(self, handle: FakeHandle) -> None:
        if self.close_error:
            raise RuntimeError("mock close failure")
        handle.exit_code = 0


class FakeOwner:
    alive = True

    def is_alive(self, pid: int) -> bool:
        del pid
        return self.alive


class GuardLifecycleTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = SnapshotStore(Path(self.temporary.name) / "state.json")
        self.store.bootstrap_normal_for_test(1.0)
        self.wallet = FakeWallet(self.store)
        self.owner = FakeOwner()
        identifiers = iter(("flow-one", "flow-two"))
        self.guard = GuardLifecycle(
            self.store, self.store.load(), self.wallet, self.owner,
            id_factory=lambda: next(identifiers),
            clock=lambda: 2.0,
        )

    def test_start_persists_entering_then_enforces_one_active_flow(self) -> None:
        started = self.guard.start_flow(101)
        self.assertTrue(started.ok)
        self.assertEqual(started.flow_id, "flow-one")
        self.assertIs(self.wallet.opened_in, GuardState.ENTERING)
        self.assertIs(self.store.load().state, GuardState.ACTIVE)
        self.assertEqual(self.guard.start_flow(101).code, "FLOW_ALREADY_ACTIVE")

    def test_matching_cancel_closes_cleanly_and_wrong_id_is_rejected(self) -> None:
        self.guard.start_flow(101)
        self.assertEqual(self.guard.cancel_flow("wrong").code, "FLOW_ID_MISMATCH")
        closing = self.guard.cancel_flow("flow-one")
        self.assertEqual(closing.state, GuardState.EXITING)
        self.assertEqual(self.guard.monitor_once().code, "FLOW_COMPLETED")
        self.assertIs(self.guard.snapshot.state, GuardState.NORMAL)

    def test_wallet_or_owner_loss_requires_matching_recovery_and_new_id(self) -> None:
        self.guard.start_flow(101)
        self.wallet.handle.exit_code = 7
        self.assertIs(self.guard.monitor_once().state, GuardState.RECOVERY_REQUIRED)
        self.assertEqual(self.guard.recover_flow("wrong").code, "FLOW_ID_MISMATCH")
        self.assertTrue(self.guard.recover_flow("flow-one").ok)
        self.wallet.handle = FakeHandle(303)
        restarted = self.guard.start_flow(101)
        self.assertEqual(restarted.flow_id, "flow-two")
        self.owner.alive = False
        self.assertEqual(self.guard.monitor_once().code, "OWNER_INTERRUPTED")

    def test_launch_and_close_failures_require_recovery(self) -> None:
        self.wallet.launch_error = True
        self.assertEqual(self.guard.start_flow(101).code, "WALLET_LAUNCH_FAILED")
        self.assertIs(self.guard.snapshot.state, GuardState.RECOVERY_REQUIRED)
        self.assertEqual(self.guard.start_flow(101).code, "RECOVERY_REQUIRED")
        self.guard.recover_flow("flow-one")
        self.wallet.launch_error = False
        self.wallet.close_error = True
        self.wallet.handle = FakeHandle(404)
        self.guard.start_flow(101)
        self.assertEqual(self.guard.cancel_flow("flow-two").code, "WALLET_INTERRUPTED")

    def test_signing_disabled_refuses_authority_commands(self) -> None:
        disabled = GuardSnapshot(GuardState.SIGNING_DISABLED, None, None, None, "TEST", 1.0)
        guard = GuardLifecycle(self.store, disabled, self.wallet, self.owner)
        self.assertEqual(guard.start_flow(101).code, "SIGNING_DISABLED")
        self.assertEqual(guard.cancel_flow("flow").code, "FLOW_NOT_ACTIVE")
        self.assertEqual(guard.recover_flow("flow").code, "RECOVERY_NOT_REQUIRED")
        self.assertTrue(guard.health().ok)

    def test_unavailable_owner_never_launches_wallet(self) -> None:
        self.owner.alive = False
        self.assertEqual(self.guard.start_flow(101).code, "OWNER_UNAVAILABLE")
        self.assertIsNone(self.wallet.opened_in)


if __name__ == "__main__":
    unittest.main()
