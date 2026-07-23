from __future__ import annotations

import tempfile
import threading
import time
import unittest
import uuid
from multiprocessing.connection import Client, Listener
from pathlib import Path

from holon_contracts import MessageKind, make_envelope
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.authority import AuthorityService
from holon_guard.wallet import WalletBalancesResult, WalletOpenResult
from holon_guard.server import GuardServer
from holon_guard_ipc import MAX_MESSAGE_BYTES, PipeClient, PipeProtocolError, PipeUnavailable
from holon_guard_ipc.codec import decode_message, validate_response
from guard_support import ACTION_ID, enabled_policy, make_audit, make_ledger, transfer_request


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

    def open_public(self) -> WalletOpenResult:
        return WalletOpenResult(
            True, "ACTIVATED", "WALLET_ACTIVATED", "Wallet is open.",
        )

    def read_public_balances(self) -> WalletBalancesResult:
        return WalletBalancesResult(
            True,
            {
                "status": "DEGRADED", "authority_available": False,
                "account": None,
                "networks": [
                    {
                        "network": network, "chain_id": chain_id,
                        "status": "UNAVAILABLE", "block_number": None,
                        "updated_at": None, "error_code": "WALLET_NOT_CREATED",
                        "balances": None,
                    }
                    for network, chain_id in (("ethereum", 1), ("base", 8453))
                ],
                "code": "WALLET_NOT_CREATED",
                "message": "Wallet has not been created.",
            },
        )


class LiveOwner:
    def is_alive(self, pid: int) -> bool:
        del pid
        return True


class GuardServerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        store = SnapshotStore(root / "guard-state.json")
        store.bootstrap_normal_for_test(1.0)
        ledger = make_ledger(root)
        lifecycle = GuardLifecycle(store, store.load(), MockWallet(), LiveOwner(), ledger)
        authority = AuthorityService(lifecycle, enabled_policy(), make_audit(root))
        self.pipe = rf"\\.\pipe\Holon.Guard.test.{uuid.uuid4()}"
        self.server = GuardServer(self.pipe, authority, 0.02)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = PipeClient(self.pipe, connect_timeout=2.0, response_timeout=1.0)
        health = self.client.request(MessageKind.HEALTH_REQUEST)
        self.assertEqual(health.payload["guard_state"], "NORMAL")

    def tearDown(self) -> None:
        self.server.stop()
        self.thread.join(timeout=2.0)
        self.assertFalse(self.thread.is_alive())

    def test_prepare_status_cancel_and_terminal_replay(self) -> None:
        started = self.client.exchange(transfer_request(), owner_pid=101)
        self.assertEqual(started.kind, MessageKind.PROTECTED_FLOW_STARTED)
        status = self.client.request(MessageKind.ACTION_STATUS_REQUEST, action_id=ACTION_ID)
        self.assertEqual(status.payload["action_state"], "AWAITING_LOCAL_CONFIRMATION")
        cancelling = self.client.request(MessageKind.CANCEL_ACTION, action_id=ACTION_ID)
        self.assertEqual(cancelling.payload["guard_state"], "EXITING")
        health = self.client.request(MessageKind.HEALTH_REQUEST)
        self.assertEqual(health.payload["guard_state"], "NORMAL")
        replay = self.client.exchange(transfer_request(), owner_pid=101)
        self.assertEqual(replay.payload["code"], "ACTION_REPLAYED")

    def test_public_open_round_trip_has_no_process_metadata_or_action(self) -> None:
        opened = self.client.request(MessageKind.OPEN_WALLET)
        self.assertEqual(opened.kind, MessageKind.WALLET_OPENED)
        self.assertNotIn("action_id", opened.to_dict())
        serialized = str(opened.to_dict()).lower()
        for field in ("pid", "path", "pipe", "launch_id", "flow_id"):
            self.assertNotIn(field, serialized)

    def test_public_balance_round_trip_has_no_process_metadata_or_action(self) -> None:
        balances = self.client.request(
            MessageKind.READ_WALLET_BALANCES, response_timeout=2.0,
        )
        self.assertEqual(balances.kind, MessageKind.WALLET_BALANCES)
        self.assertNotIn("action_id", balances.to_dict())
        serialized = str(balances.to_dict()).lower()
        for field in ("pid", "path", "pipe", "query_id", "ciphertext"):
            self.assertNotIn(field, serialized)

    def test_malformed_oversized_and_legacy_frames_are_safe(self) -> None:
        legacy = b'{"ipc_version":"1","command":"health","payload":{}}'
        for raw in (b"{broken", b"x" * (MAX_MESSAGE_BYTES + 1), legacy):
            connection = Client(self.pipe, family="AF_PIPE", authkey=None)
            with connection:
                connection.send_bytes(raw)
                frame = decode_message(connection.recv_bytes(MAX_MESSAGE_BYTES + 1))
            response = validate_response(frame)
            self.assertEqual(response.payload["code"], "IPC_INVALID_REQUEST")
            self.assertNotIn("broken", response.payload["message"])

    def test_unavailable_pipe_and_response_timeout_are_bounded(self) -> None:
        missing = rf"\\.\pipe\Holon.Guard.missing.{uuid.uuid4()}"
        with self.assertRaises(PipeUnavailable):
            PipeClient(missing, 0.02, 0.02).request(MessageKind.HEALTH_REQUEST)
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
            PipeClient(slow_pipe, 0.5, 0.02).request(MessageKind.HEALTH_REQUEST)
        thread.join(timeout=1.0)

    def test_silent_connection_does_not_stall_guard(self) -> None:
        silent = Client(self.pipe, family="AF_PIPE", authkey=None)
        time.sleep(0.05)
        silent.close()
        health = self.client.request(MessageKind.HEALTH_REQUEST)
        self.assertEqual(health.payload["code"], "OK")

    def test_transfer_intent_claimed_owner_must_match_pipe_client(self) -> None:
        request = make_envelope(
            MessageKind.TRANSFER_INTENT,
            {
                "network": "base", "asset": "usdc", "amount": "1",
                "recipient": "0x1111111111111111111111111111111111111111",
            },
            action_id=ACTION_ID,
        )
        with self.assertRaises(PipeProtocolError):
            self.client.exchange(request, owner_pid=1)
