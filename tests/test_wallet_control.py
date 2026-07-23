from __future__ import annotations

import json
import os
import sys
import threading
import uuid
from pathlib import Path

import pytest

from holon_wallet_control import (
    ControlProtocolError,
    WalletControlClient,
    WalletControlServer,
    WalletPublicClient,
)
from holon_wallet_control.public_protocol import MAX_PUBLIC_BYTES
from holon_wallet_control.protocol import _process_image


class FakeConnection:
    def __init__(self, incoming: bytes, handle: int = 44) -> None:
        self.incoming = incoming
        self.handle = handle
        self.sent: list[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def fileno(self) -> int:
        return self.handle

    def poll(self, timeout: float) -> bool:
        del timeout
        return True

    def send_bytes(self, value: bytes) -> None:
        self.sent.append(value)

    def recv_bytes(self, maxlength: int) -> bytes:
        assert len(self.incoming) <= maxlength
        return self.incoming


def response(launch_id: str, wallet_pid: int = 202, **extra: object) -> bytes:
    value = {
        "control_version": "1",
        "kind": "ready",
        "launch_id": launch_id,
        "wallet_pid": wallet_pid,
        "status": "READY",
        **extra,
    }
    return json.dumps(value, separators=(",", ":")).encode()


def public_snapshot() -> dict[str, object]:
    assets = {
        "ETH": {"asset": "ETH", "amount_atomic": "0", "decimals": 18, "display": "0 ETH"},
        "USDC": {"asset": "USDC", "amount_atomic": "0", "decimals": 6, "display": "0 USDC"},
    }
    return {
        "status": "READY", "authority_available": False,
        "account": {
            "label": "Account 1",
            "address": "0x1111111111111111111111111111111111111111",
        },
        "networks": [
            {
                "network": "ethereum", "chain_id": 1, "status": "LIVE",
                "block_number": "1", "updated_at": "2026-07-23T12:00:00Z",
                "error_code": None, "balances": assets,
            },
            {
                "network": "base", "chain_id": 8453, "status": "LIVE",
                "block_number": "2", "updated_at": "2026-07-23T12:00:00Z",
                "error_code": None, "balances": assets,
            },
        ],
        "code": "BALANCES_READY", "message": "Wallet balances are available.",
    }


def public_response(query_id: str, wallet_pid: int = 202, **extra: object) -> bytes:
    value = {
        "public_version": "1", "kind": "active_balances",
        "query_id": query_id, "wallet_pid": wallet_pid,
        "snapshot": public_snapshot(), **extra,
    }
    return json.dumps(value, separators=(",", ":")).encode()


def test_client_binds_launch_pid_and_exact_process_image(tmp_path: Path) -> None:
    launch_id = str(uuid.uuid4())
    expected = tmp_path / "HolonWallet.exe"
    connection = FakeConnection(response(launch_id))
    client = WalletControlClient(
        pipe_name="fixture",
        connector=lambda *args, **kwargs: connection,
        waiter=lambda name, timeout: None,
        peer_pid=lambda handle: 202,
        process_image=lambda pid: expected,
    )
    assert client.activate(launch_id, expected, 0.2) == 202
    request = json.loads(connection.sent[0])
    assert request == {
        "control_version": "1", "kind": "activate", "launch_id": launch_id,
    }


@pytest.mark.parametrize(
    ("incoming", "peer_pid", "image"),
    [
        (lambda value: response(str(uuid.uuid4())), 202, "expected"),
        (lambda value: response(value, 303), 202, "expected"),
        (lambda value: response(value, unexpected="field"), 202, "expected"),
        (lambda value: response(value), 202, "wrong"),
    ],
)
def test_client_refuses_correlation_pid_fields_and_path(
    tmp_path: Path, incoming, peer_pid: int, image: str,
) -> None:
    launch_id = str(uuid.uuid4())
    expected = tmp_path / "expected" / "HolonWallet.exe"
    actual = expected if image == "expected" else tmp_path / "wrong" / "HolonWallet.exe"
    client = WalletControlClient(
        pipe_name="fixture",
        connector=lambda *args, **kwargs: FakeConnection(incoming(launch_id)),
        waiter=lambda name, timeout: None,
        peer_pid=lambda handle: peer_pid,
        process_image=lambda pid: actual,
    )
    with pytest.raises(ControlProtocolError):
        client.activate(launch_id, expected, 0.2)


def test_public_client_binds_query_pid_and_exact_process_image(tmp_path: Path) -> None:
    query_id = str(uuid.uuid4())
    expected = tmp_path / "HolonWallet.exe"
    connection = FakeConnection(public_response(query_id))
    client = WalletPublicClient(
        pipe_name="fixture",
        connector=lambda *args, **kwargs: connection,
        waiter=lambda name, timeout: None,
        peer_pid=lambda handle: 202,
        process_image=lambda pid: expected,
    )
    assert client.read(query_id, expected, 0.2, 0.3) == public_snapshot()
    assert json.loads(connection.sent[0]) == {
        "public_version": "1", "kind": "read_active_balances",
        "query_id": query_id,
    }


@pytest.mark.parametrize(
    ("incoming", "peer_pid", "image"),
    [
        (lambda value: public_response(str(uuid.uuid4())), 202, "expected"),
        (lambda value: public_response(value, 303), 202, "expected"),
        (lambda value: public_response(value, unexpected="field"), 202, "expected"),
        (lambda value: public_response(value), 202, "wrong"),
    ],
)
def test_public_client_refuses_correlation_pid_fields_and_path(
    tmp_path: Path, incoming, peer_pid: int, image: str,
) -> None:
    query_id = str(uuid.uuid4())
    expected = tmp_path / "expected" / "HolonWallet.exe"
    actual = expected if image == "expected" else tmp_path / "wrong" / "HolonWallet.exe"
    client = WalletPublicClient(
        pipe_name="fixture",
        connector=lambda *args, **kwargs: FakeConnection(incoming(query_id)),
        waiter=lambda name, timeout: None,
        peer_pid=lambda handle: peer_pid,
        process_image=lambda pid: actual,
    )
    with pytest.raises(ControlProtocolError):
        client.read(query_id, expected, 0.2, 0.3)


def test_public_client_refuses_oversized_payload(tmp_path: Path) -> None:
    query_id = str(uuid.uuid4())
    client = WalletPublicClient(
        pipe_name="fixture",
        connector=lambda *args, **kwargs: FakeConnection(b"x" * (MAX_PUBLIC_BYTES + 1)),
        waiter=lambda name, timeout: None,
        peer_pid=lambda handle: 202,
        process_image=lambda pid: tmp_path / "HolonWallet.exe",
    )
    with pytest.raises(ControlProtocolError):
        client.read(query_id, tmp_path / "HolonWallet.exe", 0.2, 0.3)


@pytest.mark.skipif(sys.platform != "win32", reason="Windows control pipe")
def test_real_control_pipe_activates_and_stops_cleanly() -> None:
    activated = threading.Event()
    pipe = rf"\\.\pipe\Holon.Wallet.Control.test.{uuid.uuid4()}"
    server = WalletControlServer(activated.set, pipe_name=pipe)
    server.start()
    thread = server._thread
    try:
        pid = WalletControlClient(pipe_name=pipe).activate(
            str(uuid.uuid4()), _process_image(os.getpid()), 1.0,
        )
        assert pid > 0
        assert activated.wait(1.0)
    finally:
        server.stop()
    assert thread is not None and not thread.is_alive()
