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
)
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
