from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from holon_contracts import MessageKind, make_envelope
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.authority import AuthorityService
from holon_guard.server import GuardServer
from holon_guard_ipc.codec import decode_message, encode_message, validate_response
from guard_support import enabled_policy, make_audit, make_ledger, transfer_request


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

    def fileno(self) -> int:
        return 1

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
        self.audit = make_audit(root)
        self.authority = AuthorityService(lifecycle, enabled_policy(), self.audit)
        self.server = GuardServer(
            "unused", self.authority, client_pid_probe=lambda _handle: 101,
        )

    def exchange_raw(self, message: dict, owner_pid: int = 101) -> object:
        connection = Connection({
            "ipc_version": "1", "message": message, "owner_pid": owner_pid,
        })
        self.server._handle_connection(connection)
        return validate_response(decode_message(connection.response))

    @staticmethod
    def owner_requests() -> tuple:
        transfer_intent = make_envelope(
            MessageKind.TRANSFER_INTENT,
            {
                "network": "base", "asset": "usdc", "amount": "1",
                "recipient": "0x1111111111111111111111111111111111111111",
            },
            action_id="act-33333333-3333-4333-8333-333333333333",
        )
        lending_intent = make_envelope(
            MessageKind.LENDING_AUTHORITY_INTENT,
            {
                "module_id": "lending", "module_version": "1",
                "protocol_profile_id": "aave-v3-base-usdc",
                "protocol_profile_version": "1", "network": "base",
                "asset": "usdc", "beneficiary_mode": "active_wallet_account",
                "action": "supply", "amount_mode": "exact", "amount": "1",
            },
            action_id="act-44444444-4444-4444-8444-444444444444",
        )
        return transfer_request(), transfer_intent, lending_intent

    def test_all_owner_required_kinds_bind_to_pipe_client_before_dispatch(self) -> None:
        for request in self.owner_requests():
            with self.subTest(kind=request.kind.value):
                with patch.object(
                    self.authority,
                    "handle",
                    side_effect=lambda envelope, _owner: self.authority.refusal(
                        envelope, "REQUEST_INVALID", "Refused.",
                    ),
                ) as handle:
                    matched = self.exchange_raw(request.to_dict())
                    self.assertEqual(matched.kind, MessageKind.REFUSAL)
                    handle.assert_called_once()

                with patch.object(self.authority, "handle") as handle:
                    mismatch = self.exchange_raw(request.to_dict(), owner_pid=202)
                    self.assertEqual(mismatch.kind, MessageKind.ERROR)
                    handle.assert_not_called()

    def test_owner_pid_probe_failure_never_dispatches_authority(self) -> None:
        self.server._client_pid_probe = lambda _handle: (_ for _ in ()).throw(
            RuntimeError("probe failed"),
        )
        with patch.object(self.authority, "handle") as handle:
            response = self.exchange_raw(transfer_request().to_dict())
        self.assertEqual(response.kind, MessageKind.ERROR)
        handle.assert_not_called()

    def test_arbitrary_call_is_deterministic_and_never_reaches_wallet(self) -> None:
        message = transfer_request().to_dict()
        message["payload"]["calldata"] = "private-input"
        response = self.exchange_raw(message)
        self.assertEqual(response.kind, MessageKind.REFUSAL)
        self.assertEqual(response.payload["code"], "ARBITRARY_CALL_REFUSED")
        self.assertNotIn("private-input", str(response.to_dict()))
        self.assertNotIn(
            "private-input", str([event.to_dict() for event in self.audit.journal.events()])
        )
        self.assertEqual(self.wallet.calls, 0)

    def test_schema_mismatch_returns_safe_compatibility_status(self) -> None:
        message = transfer_request().to_dict()
        message["schema_version"] = "2"
        response = self.exchange_raw(message)
        self.assertEqual(response.kind, MessageKind.COMPATIBILITY_STATUS)
        self.assertEqual(response.payload["code"], "SCHEMA_VERSION_UNSUPPORTED")
        self.assertEqual(self.wallet.calls, 0)
