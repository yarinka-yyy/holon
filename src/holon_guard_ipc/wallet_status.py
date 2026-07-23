"""Strict Wallet-to-Guard public authority lifecycle callback channel."""

from __future__ import annotations

import ctypes
import json
import os
import re
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from ctypes import wintypes
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path

STATUS_VERSION = "1"
STATUS_PIPE_NAME = r"\\.\pipe\Holon.Guard.Wallet.v1"
MAX_STATUS_BYTES = 8 * 1024
STATUS_FIELDS = frozenset({
    "status_version", "kind", "flow_id", "action_id", "prepared_digest",
    "wallet_pid", "event", "code", "outcome",
})
ACK_FIELDS = frozenset({"status_version", "kind", "flow_id", "action_id", "status"})
ACTION_RE = re.compile(r"^act-[0-9a-f-]{36}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")


class WalletStatusError(RuntimeError):
    pass


def _wait_pipe(pipe_name: str, timeout: float) -> None:
    if sys.platform != "win32":
        raise WalletStatusError("Guard status pipe is unavailable")
    wait = ctypes.WinDLL("kernel32", use_last_error=True).WaitNamedPipeW
    wait.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    wait.restype = ctypes.c_int
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        remaining = max(1, min(100, int((deadline - time.monotonic()) * 1000)))
        if wait(pipe_name, remaining):
            return
        if time.monotonic() >= deadline:
            raise WalletStatusError("Guard status pipe is unavailable")
        time.sleep(0.05)


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
        raise WalletStatusError("Client process verification failed")
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise WalletStatusError("Client process verification failed")
        return Path(buffer.value)
    finally:
        kernel32.CloseHandle(handle)


def _same_path(actual: Path, expected: Path) -> bool:
    return os.path.normcase(os.path.abspath(actual)) == os.path.normcase(
        os.path.abspath(expected)
    )


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise WalletStatusError("Invalid status identifier")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise WalletStatusError("Invalid status identifier") from error
    if parsed.version != 4 or str(parsed) != value:
        raise WalletStatusError("Invalid status identifier")
    return value


def _encode(value: Mapping[str, object]) -> bytes:
    try:
        raw = json.dumps(dict(value), separators=(",", ":"), sort_keys=True).encode()
    except (TypeError, ValueError) as error:
        raise WalletStatusError("Invalid status message") from error
    if len(raw) > MAX_STATUS_BYTES:
        raise WalletStatusError("Status message is too large")
    return raw


def _decode(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes) or len(raw) > MAX_STATUS_BYTES:
        raise WalletStatusError("Invalid status message size")
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise WalletStatusError("Invalid status message") from error
    if not isinstance(value, dict):
        raise WalletStatusError("Invalid status message")
    return value


def validate_update(value: Mapping[str, object]) -> dict[str, object]:
    if set(value) != STATUS_FIELDS or value.get("status_version") != STATUS_VERSION or value.get("kind") != "transfer_status":
        raise WalletStatusError("Invalid status update")
    _uuid(value.get("flow_id"))
    action = value.get("action_id")
    if not isinstance(action, str) or ACTION_RE.fullmatch(action) is None:
        raise WalletStatusError("Invalid status update")
    _uuid(action[4:])
    digest, code = value.get("prepared_digest"), value.get("code")
    if (
        not isinstance(digest, str) or HEX_RE.fullmatch(digest) is None
        or type(value.get("wallet_pid")) is not int or value["wallet_pid"] <= 0
        or value.get("event") not in {"REJECTED", "FAILED", "COMPLETED"}
        or not isinstance(code, str) or CODE_RE.fullmatch(code) is None
    ):
        raise WalletStatusError("Invalid status update")
    outcome = value.get("outcome")
    if value["event"] == "COMPLETED":
        if outcome not in {"pending", "confirmed", "unknown"}:
            raise WalletStatusError("Invalid status update")
    elif outcome is not None:
        raise WalletStatusError("Invalid status update")
    return dict(value)


def _client_pid(handle: int) -> int:
    if sys.platform != "win32":
        raise WalletStatusError("Client process verification is unavailable")
    process_id = wintypes.ULONG()
    call = ctypes.WinDLL("kernel32", use_last_error=True).GetNamedPipeClientProcessId
    call.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    call.restype = wintypes.BOOL
    if not call(handle, ctypes.byref(process_id)) or process_id.value <= 0:
        raise WalletStatusError("Client process verification failed")
    return int(process_id.value)


class WalletStatusClient:
    def __init__(self, pipe_name: str = STATUS_PIPE_NAME, connector: Callable[..., Connection] = Client, waiter: Callable[[str, float], None] = _wait_pipe, wallet_pid: Callable[[], int] = os.getpid) -> None:
        self.pipe_name, self._connector, self._waiter, self._wallet_pid = pipe_name, connector, waiter, wallet_pid

    def send(self, update: Mapping[str, object], timeout: float = 1.0) -> None:
        payload = dict(update)
        payload["wallet_pid"] = self._wallet_pid()
        checked = validate_update(payload)
        self._waiter(self.pipe_name, timeout)
        try:
            connection = self._connector(self.pipe_name, family="AF_PIPE", authkey=None)
            with connection:
                connection.send_bytes(_encode(checked))
                if not connection.poll(timeout):
                    raise WalletStatusError("Guard status acknowledgement timed out")
                response = _decode(connection.recv_bytes(MAX_STATUS_BYTES + 1))
        except WalletStatusError:
            raise
        except Exception as error:
            raise WalletStatusError("Guard status callback failed") from error
        if set(response) != ACK_FIELDS or response.get("status_version") != STATUS_VERSION or response.get("kind") != "status_received" or response.get("flow_id") != checked["flow_id"] or response.get("action_id") != checked["action_id"] or response.get("status") != "ACCEPTED":
            raise WalletStatusError("Invalid Guard status acknowledgement")


class WalletStatusServer:
    def __init__(self, handler: Callable[[dict[str, object]], bool], expected_peer: Callable[[], tuple[int | None, Path | None]], pipe_name: str = STATUS_PIPE_NAME, listener_factory: Callable[..., Listener] = Listener, peer_pid: Callable[[int], int] = _client_pid, process_image: Callable[[int], Path] = _process_image, invalid_handler: Callable[[str], None] | None = None) -> None:
        self._handler, self._expected_peer = handler, expected_peer
        self.pipe_name, self._listener_factory = pipe_name, listener_factory
        self._peer_pid, self._process_image = peer_pid, process_image
        self._invalid_handler = invalid_handler
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def _invalidate(self, code: str) -> None:
        if self._invalid_handler is None:
            return
        threading.Thread(
            target=self._invalid_handler,
            args=(code,),
            name="holon-guard-wallet-status-invalid",
            daemon=True,
        ).start()

    def start(self) -> None:
        self._listener = self._listener_factory(self.pipe_name, family="AF_PIPE", authkey=None)
        self._thread = threading.Thread(target=self._serve, name="holon-guard-wallet-status", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                connection = self._listener.accept()
            except (OSError, EOFError):
                return
            try:
                with connection:
                    if not connection.poll(1.0):
                        continue
                    actual_pid = self._peer_pid(connection.fileno())
                    update = validate_update(_decode(connection.recv_bytes(MAX_STATUS_BYTES + 1)))
                    expected_pid, expected_path = self._expected_peer()
                    if actual_pid != update["wallet_pid"] or actual_pid != expected_pid or expected_path is None or not _same_path(self._process_image(actual_pid), expected_path) or not self._handler(update):
                        self._invalidate("WALLET_STATUS_MISMATCH")
                        continue
                    connection.send_bytes(_encode({"status_version": STATUS_VERSION, "kind": "status_received", "flow_id": update["flow_id"], "action_id": update["action_id"], "status": "ACCEPTED"}))
            except Exception:
                if not self._stop.is_set():
                    self._invalidate("WALLET_STATUS_INVALID")
                continue

    def stop(self) -> None:
        self._stop.set()
        listener, self._listener = self._listener, None
        if listener is not None:
            try:
                wake = Client(self.pipe_name, family="AF_PIPE", authkey=None); wake.close()
            except Exception:
                pass
            try:
                listener.close()
            except Exception:
                pass
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
