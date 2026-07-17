from __future__ import annotations

import tempfile
import unittest
import uuid
from pathlib import Path

from holon_guard import GuardLifecycle, SnapshotStore


class Handle:
    pid = 202

    def poll(self) -> None:
        return None


class Wallet:
    def open_or_activate(self, flow_id: str) -> Handle:
        del flow_id
        return Handle()

    def request_close(self, handle: Handle) -> None:
        del handle


class Owner:
    def is_alive(self, pid: int) -> bool:
        del pid
        return True


class GuardIdentifierTests(unittest.TestCase):
    def test_guard_generates_uuid4_flow_identifier(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            store = SnapshotStore(Path(temporary) / "state.json")
            snapshot = store.bootstrap_normal_for_test(1.0)
            result = GuardLifecycle(store, snapshot, Wallet(), Owner()).start_flow(101)
        identifier = uuid.UUID(result.flow_id)
        self.assertEqual(identifier.version, 4)
        self.assertEqual(str(identifier), result.flow_id)


if __name__ == "__main__":
    unittest.main()
