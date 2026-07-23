"""Strict, small Wallet activation protocol with no authority or secrets."""

from __future__ import annotations

import ctypes
import json
import os
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from ctypes import wintypes
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path

CONTROL_VERSION = "1"
CONTROL_PIPE_NAME = r"\\.\pipe\Holon.Wallet.Control.v1"
MAX_CONTROL_BYTES = 2 * 1024
REQUEST_FIELDS = frozenset({"control_version", "kind", "launch_id"})
RESPONSE_FIELDS = frozenset(
    {"control_version", "kind", "launch_id", "wallet_pid", "status"},
)


class ControlUnavailable(ConnectionError):
    pass


class ControlProtocolError(RuntimeError):
    pass


def _launch_id(value: object) -> str:
    if not isinstance(value, str):
        raise ControlProtocolError("Invalid launch identifier")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ControlProtocolError("Invalid launch identifier") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ControlProtocolError("Invalid launch identifier")
    return value


def _encode(value: Mapping[str, object]) -> bytes:
    try:
        raw = json.dumps(
            dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ControlProtocolError("Invalid control message") from error
    if len(raw) > MAX_CONTROL_BYTES:
        raise ControlProtocolError("Control message is too large")
    return raw


def _decode(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes) or len(raw) > MAX_CONTROL_BYTES:
        raise ControlProtocolError("Invalid control message size")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControlProtocolError("Invalid control message") from error
    if not isinstance(value, dict):
        raise ControlProtocolError("Invalid control message")
    return value


def _request(launch_id: str) -> dict[str, object]:
    return {
        "control_version": CONTROL_VERSION,
        "kind": "activate",
        "launch_id": _launch_id(launch_id),
    }


def _parse_request(value: Mapping[str, object]) -> str:
    if (
        set(value) != REQUEST_FIELDS
        or value.get("control_version") != CONTROL_VERSION
        or value.get("kind") != "activate"
    ):
        raise ControlProtocolError("Invalid control request")
    return _launch_id(value.get("launch_id"))


def _response(launch_id: str, wallet_pid: int) -> dict[str, object]:
    if type(wallet_pid) is not int or wallet_pid <= 0:
        raise ControlProtocolError("Invalid Wallet process")
    return {
        "control_version": CONTROL_VERSION,
        "kind": "ready",
        "launch_id": _launch_id(launch_id),
        "wallet_pid": wallet_pid,
        "status": "READY",
    }


def _parse_response(value: Mapping[str, object], launch_id: str) -> int:
    if (
        set(value) != RESPONSE_FIELDS
        or value.get("control_version") != CONTROL_VERSION
        or value.get("kind") != "ready"
        or value.get("status") != "READY"
        or value.get("launch_id") != launch_id
    ):
        raise ControlProtocolError("Invalid control response")
    wallet_pid = value.get("wallet_pid")
    if type(wallet_pid) is not int or wallet_pid <= 0:
        raise ControlProtocolError("Invalid Wallet process")
    return wallet_pid


def _wait_pipe(pipe_name: str, timeout: float) -> None:
    if sys.platform != "win32":
        raise ControlUnavailable("Wallet control pipe is unavailable")
    wait = ctypes.WinDLL("kernel32", use_last_error=True).WaitNamedPipeW
    wait.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    wait.restype = ctypes.c_int
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        remaining = max(1, min(100, int((deadline - time.monotonic()) * 1000)))
        if wait(pipe_name, remaining):
            return
        if time.monotonic() >= deadline:
            raise ControlUnavailable("Wallet control pipe is unavailable")
        time.sleep(0.05)


def _server_pid(handle: int) -> int:
    if sys.platform != "win32":
        raise ControlProtocolError("Wallet process verification is unavailable")
    process_id = wintypes.ULONG()
    call = ctypes.WinDLL("kernel32", use_last_error=True).GetNamedPipeServerProcessId
    call.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    call.restype = wintypes.BOOL
    if not call(handle, ctypes.byref(process_id)) or process_id.value <= 0:
        raise ControlProtocolError("Wallet process verification failed")
    return int(process_id.value)


def _process_image(pid: int) -> Path:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        raise ControlProtocolError("Wallet process verification failed")
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise ControlProtocolError("Wallet process verification failed")
        return Path(buffer.value)
    finally:
        kernel32.CloseHandle(handle)


def _same_path(actual: Path, expected: Path) -> bool:
    return os.path.normcase(os.path.abspath(actual)) == os.path.normcase(
        os.path.abspath(expected),
    )


class WalletControlClient:
    def __init__(
        self,
        pipe_name: str = CONTROL_PIPE_NAME,
        connector: Callable[..., Connection] = Client,
        waiter: Callable[[str, float], None] = _wait_pipe,
        peer_pid: Callable[[int], int] = _server_pid,
        process_image: Callable[[int], Path] = _process_image,
    ) -> None:
        self.pipe_name = pipe_name
        self._connector = connector
        self._waiter = waiter
        self._peer_pid = peer_pid
        self._process_image = process_image

    def activate(self, launch_id: str, expected_path: Path, timeout: float) -> int:
        request = _encode(_request(launch_id))
        self._waiter(self.pipe_name, timeout)
        try:
            connection = self._connector(self.pipe_name, family="AF_PIPE", authkey=None)
        except Exception as error:
            raise ControlUnavailable("Wallet control connection failed") from error
        try:
            with connection:
                peer_pid = self._peer_pid(connection.fileno())
                connection.send_bytes(request)
                if not connection.poll(timeout):
                    raise ControlProtocolError("Wallet control response timed out")
                response = _decode(connection.recv_bytes(MAX_CONTROL_BYTES + 1))
        except (ControlProtocolError, ControlUnavailable):
            raise
        except Exception as error:
            raise ControlProtocolError("Wallet control response failed") from error
        wallet_pid = _parse_response(response, launch_id)
        if wallet_pid != peer_pid or not _same_path(
            self._process_image(peer_pid), expected_path,
        ):
            raise ControlProtocolError("Wallet process verification failed")
        return wallet_pid


class WalletControlServer:
    def __init__(
        self,
        activate: Callable[[], None],
        pipe_name: str = CONTROL_PIPE_NAME,
        listener_factory: Callable[..., Listener] = Listener,
        wallet_pid: Callable[[], int] = os.getpid,
    ) -> None:
        self._activate = activate
        self.pipe_name = pipe_name
        self._listener_factory = listener_factory
        self._wallet_pid = wallet_pid
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        if self._thread is not None:
            return
        try:
            self._listener = self._listener_factory(
                self.pipe_name, family="AF_PIPE", authkey=None,
            )
        except Exception as error:
            raise ControlUnavailable("Wallet control server could not start") from error
        self._thread = threading.Thread(
            target=self._serve, name="holon-wallet-control", daemon=True,
        )
        self._thread.start()

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                connection = self._listener.accept()
            except (OSError, EOFError):
                return
            if self._stop.is_set():
                connection.close()
                return
            self._handle(connection)

    def _handle(self, connection: Connection) -> None:
        try:
            with connection:
                if not connection.poll(1.0):
                    return
                launch_id = _parse_request(
                    _decode(connection.recv_bytes(MAX_CONTROL_BYTES + 1)),
                )
                self._activate()
                connection.send_bytes(_encode(_response(launch_id, self._wallet_pid())))
        except Exception:
            return

    def stop(self) -> None:
        self._stop.set()
        listener = self._listener
        self._listener = None
        if listener is not None:
            try:
                wake = Client(self.pipe_name, family="AF_PIPE", authkey=None)
                wake.close()
            except Exception:
                pass
            try:
                listener.close()
            except Exception:
                pass
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.join(timeout=1.0)
