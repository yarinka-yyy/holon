from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from holon_guard_ipc.policy_control import (
    POLICY_CONTROL_VERSION, PolicyControlClient, PolicyControlServer,
    _decode, _encode, validate_request,
)
from holon_guard_ipc.policy_control import ControlProtocolError


class FakeConnection:
    def __init__(self, incoming: bytes = b"", handle: int = 7) -> None:
        self.incoming = incoming
        self.sent = b""
        self.handle = handle

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def fileno(self) -> int:
        return self.handle

    def poll(self, _timeout: float) -> bool:
        return True

    def recv_bytes(self, _maximum: int) -> bytes:
        return self.incoming

    def send_bytes(self, value: bytes) -> None:
        self.sent = value


class OneConnectionListener:
    def __init__(self, connection: FakeConnection) -> None:
        self.connection = connection
        self.used = False

    def accept(self) -> FakeConnection:
        if self.used:
            raise EOFError
        self.used = True
        return self.connection


def apply_request(pid: int = 44) -> dict[str, object]:
    return {
        "policy_control_version": POLICY_CONTROL_VERSION,
        "kind": "apply_draft",
        "request_id": str(uuid.uuid4()),
        "wallet_pid": pid,
        "expected_policy_revision": 0,
        "expected_policy_digest": "1" * 64,
        "reviewed_draft_digest": "2" * 64,
        "candidate_policy_digest": "3" * 64,
    }


def response(request_id: str, kind: str = "policy_applied") -> dict[str, object]:
    return {
        "policy_control_version": POLICY_CONTROL_VERSION,
        "kind": kind,
        "request_id": request_id,
        "code": "POLICY_REVISION_APPLIED",
        "policy_revision": 1,
        "policy_digest": "3" * 64,
    }


def test_policy_control_schema_has_only_digest_metadata() -> None:
    request = apply_request()
    assert validate_request(request) == request
    raw = _encode(request)
    assert b"password" not in raw and b"recipient_labels" not in raw
    for changed in (
        dict(request, password="secret"),
        dict(request, policy={}),
        dict(request, candidate_policy_digest="x" * 64),
        dict(request, expected_policy_revision=-1),
    ):
        with pytest.raises(ControlProtocolError):
            validate_request(changed)


def test_client_correlates_response_and_verifies_guard_path(tmp_path) -> None:
    guard = tmp_path / "HolonGuard.exe"
    request_id = str(uuid.uuid4())
    connection = FakeConnection(_encode(response(request_id, "policy_status")))
    client = PolicyControlClient(
        guard,
        connector=lambda *_args, **_kwargs: connection,
        waiter=lambda *_args: None,
        peer_pid=lambda _handle: 91,
        process_image=lambda _pid: guard,
        wallet_pid=lambda: 44,
    )
    monkey_request = {
        "policy_control_version": POLICY_CONTROL_VERSION,
        "kind": "policy_status",
        "request_id": request_id,
        "wallet_pid": 44,
    }
    assert client._exchange(monkey_request, 1.0)["policy_revision"] == 1

    client._process_image = lambda _pid: tmp_path / "Other.exe"
    with pytest.raises(ControlProtocolError):
        client._exchange(monkey_request, 1.0)


def test_server_verifies_wallet_pid_and_path(tmp_path) -> None:
    wallet = tmp_path / "HolonWallet.exe"
    request = apply_request()
    connection = FakeConnection(_encode(request))
    listener = OneConnectionListener(connection)
    seen = []
    server = PolicyControlServer(
        lambda value: seen.append(value) or response(str(value["request_id"])),
        wallet,
        listener_factory=lambda *_args, **_kwargs: listener,
        peer_pid=lambda _handle: 44,
        process_image=lambda _pid: wallet,
    )
    server._listener = listener
    server._serve()
    assert seen == [request]
    assert _decode(connection.sent)["kind"] == "policy_applied"

    rejected = FakeConnection(_encode(request))
    server._listener = OneConnectionListener(rejected)
    server._process_image = lambda _pid: tmp_path / "Other.exe"
    server._serve()
    assert rejected.sent == b""
