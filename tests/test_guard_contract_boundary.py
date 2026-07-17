from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from holon_contracts import MessageKind
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.authority import AuthorityService
from holon_guard.server import GuardServer
from holon_guard_ipc.codec import decode_message, encode_message, validate_response
from guard_support import enabled_policy, make_ledger, transfer_request


class Wallet:
    calls = 0

    def open_or_activate(self, flow_id: str) -> object:
        del flow_id
        self.calls += 1
        raise AssertionError("invalid contract reached Wallet")

    def request_close(self, handle: object) -> None:
        del handle


class Owner:
    def is_alive(self, pid: int) -> bool:
        return pid > 0


class Connection:
    def __init__(self, request: dict) -> None:
        self.raw = encode_message(request)
        self.response = b""

    def poll(self, timeout: float) -> bool:
        return timeout > 0

    def recv_bytes(self, maximum: int) -> bytes:
        self.assert_bounded(maximum)
        return self.raw

    def send_bytes(self, response: bytes) -> None:
        self.response = response

    def close(self) -> None:
        return

    @staticmethod
    def assert_bounded(maximum: int) -> None:
        if maximum <= 0:
            raise AssertionError("unbounded receive")


class GuardContractBoundaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        store = SnapshotStore(root / "guard-state.json")
        store.bootstrap_normal_for_test()
        self.wallet = Wallet()
        lifecycle = GuardLifecycle(
            store, store.load(), self.wallet, Owner(), make_ledger(root)
        )
        self.server = GuardServer("unused", AuthorityService(lifecycle, enabled_policy()))

    def exchange_raw(self, message: dict) -> object:
        connection = Connection({"ipc_version": "1", "message": message, "owner_pid": 101})
        self.server._handle_connection(connection)
        return validate_response(decode_message(connection.response))

    def test_arbitrary_call_is_deterministic_and_never_reaches_wallet(self) -> None:
        message = transfer_request().to_dict()
        message["payload"]["calldata"] = "private-input"
        response = self.exchange_raw(message)
        self.assertEqual(response.kind, MessageKind.REFUSAL)
        self.assertEqual(response.payload["code"], "ARBITRARY_CALL_REFUSED")
        self.assertNotIn("private-input", str(response.to_dict()))
        self.assertEqual(self.wallet.calls, 0)

    def test_schema_mismatch_returns_safe_compatibility_status(self) -> None:
        message = transfer_request().to_dict()
        message["schema_version"] = "2"
        response = self.exchange_raw(message)
        self.assertEqual(response.kind, MessageKind.COMPATIBILITY_STATUS)
        self.assertEqual(response.payload["code"], "SCHEMA_VERSION_UNSUPPORTED")
        self.assertEqual(self.wallet.calls, 0)
