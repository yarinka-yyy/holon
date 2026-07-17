from __future__ import annotations

import tempfile
import threading
import time
import unittest
import uuid
from multiprocessing.connection import Client, Listener
from pathlib import Path

from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.server import GuardServer
from holon_guard_ipc import MAX_MESSAGE_BYTES, PipeClient, PipeProtocolError, PipeUnavailable
from holon_guard_ipc.codec import decode_message


class RunningHandle:
    pid = 202
    exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


class MockWallet:
    def __init__(self) -> None:
        self.handle = RunningHandle()

    def open_or_activate(self, flow_id: str) -> RunningHandle:
        del flow_id
        return self.handle

    def request_close(self, handle: RunningHandle) -> None:
        handle.exit_code = 0


class LiveOwner:
    def is_alive(self, pid: int) -> bool:
        del pid
        return True


class GuardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        store = SnapshotStore(root / "state.json")
        store.bootstrap_normal_for_test(1.0)
        lifecycle = GuardLifecycle(store, store.load(), MockWallet(), LiveOwner())
        self.pipe = rf"\\.\pipe\Holon.Guard.test.{uuid.uuid4()}"
        self.server = GuardServer(self.pipe, lifecycle, 0.02)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = PipeClient(self.pipe, connect_timeout=2.0, response_timeout=1.0)
        self.assertEqual(self.client.command("health")["state"], "NORMAL")

    def tearDown(self) -> None:
        self.server.stop()
        self.thread.join(timeout=2.0)
        self.assertFalse(self.thread.is_alive())

    def test_health_start_cancel_and_recovery_calls(self) -> None:
        started = self.client.command("start_flow", {"owner_pid": 101})
        self.assertTrue(started["ok"])
        flow_id = started["flow_id"]
        mismatch = self.client.command("cancel_flow", {"flow_id": "wrong"})
        self.assertEqual(mismatch["code"], "FLOW_ID_MISMATCH")
        exiting = self.client.command("cancel_flow", {"flow_id": flow_id})
        self.assertEqual(exiting["state"], "EXITING")
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline:
            health = self.client.command("health")
            if health["state"] == "NORMAL":
                break
        self.assertEqual(health["state"], "NORMAL")

    def test_malformed_and_oversized_messages_are_safely_normalized(self) -> None:
        for raw in (b"{broken", b"x" * (MAX_MESSAGE_BYTES + 1)):
            connection = Client(self.pipe, family="AF_PIPE", authkey=None)
            with connection:
                connection.send_bytes(raw)
                response = decode_message(connection.recv_bytes(MAX_MESSAGE_BYTES + 1))
            self.assertEqual(response["code"], "IPC_INVALID_REQUEST")
            self.assertNotIn("broken", response["message"])
        self.assertEqual(self.client.command("health")["code"], "OK")

    def test_unavailable_pipe_and_response_timeout_are_bounded(self) -> None:
        missing = rf"\\.\pipe\Holon.Guard.missing.{uuid.uuid4()}"
        with self.assertRaises(PipeUnavailable):
            PipeClient(missing, 0.02, 0.02).command("health")

        slow_pipe = rf"\\.\pipe\Holon.Guard.slow.{uuid.uuid4()}"
        ready = threading.Event()

        def slow_server() -> None:
            listener = Listener(slow_pipe, family="AF_PIPE", authkey=None)
            ready.set()
            with listener.accept() as connection:
                connection.recv_bytes()
                time.sleep(0.1)
            listener.close()

        thread = threading.Thread(target=slow_server, daemon=True)
        thread.start()
        ready.wait(1.0)
        with self.assertRaises(PipeProtocolError):
            PipeClient(slow_pipe, 0.5, 0.02).command("health")
        thread.join(timeout=1.0)

    def test_silent_connection_does_not_stall_guard(self) -> None:
        silent = Client(self.pipe, family="AF_PIPE", authkey=None)
        time.sleep(0.05)
        silent.close()
        self.assertEqual(self.client.command("health")["code"], "OK")


if __name__ == "__main__":
    unittest.main()
