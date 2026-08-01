"""Strict Wallet-to-Guard transfer-policy revision channel."""

from __future__ import annotations

import json
import os
import ctypes
import sys
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path
from ctypes import wintypes

from .wallet_status import _client_pid

POLICY_CONTROL_VERSION = "2"
POLICY_CONTROL_PIPE_NAME = r"\\.\pipe\Holon.Guard.Policy.v2"
MAX_POLICY_CONTROL_BYTES = 4096
STATUS_FIELDS = frozenset({
    "policy_control_version", "kind", "request_id", "wallet_pid",
})
APPLY_FIELDS = frozenset({
    "policy_control_version", "kind", "request_id", "wallet_pid",
    "expected_policy_revision", "expected_policy_digest",
    "reviewed_draft_digest", "candidate_policy_digest",
})
CAPABILITY_FIELDS = APPLY_FIELDS | {"capability"}
INITIALIZE_FIELDS = STATUS_FIELDS | {
    "expected_policy_revision", "expected_policy_digest", "capability",
}
OPERATION_FIELDS = STATUS_FIELDS | {"operation_id"}
RESUME_OPERATION_FIELDS = OPERATION_FIELDS | {
    "phase_action_id", "transaction_hash", "receipt_state",
}
RESPONSE_FIELDS = frozenset({
    "policy_control_version", "kind", "request_id", "code",
    "policy_revision", "policy_digest", "transfer_authority_enabled",
    "lending_authority_enabled", "source_draft_digest", "authority_state",
})


class ControlUnavailable(ConnectionError):
    pass


class ControlProtocolError(RuntimeError):
    pass


def _wait_pipe(pipe_name: str, timeout: float) -> None:
    if sys.platform != "win32":
        raise ControlUnavailable("Guard policy control is unavailable")
    wait = ctypes.WinDLL("kernel32", use_last_error=True).WaitNamedPipeW
    wait.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    wait.restype = ctypes.c_int
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        remaining = max(1, min(100, int((deadline - time.monotonic()) * 1000)))
        if wait(pipe_name, remaining):
            return
        if time.monotonic() >= deadline:
            raise ControlUnavailable("Guard policy control is unavailable")
        time.sleep(0.05)


def _server_pid(handle: int) -> int:
    if sys.platform != "win32":
        raise ControlProtocolError("Guard process verification is unavailable")
    process_id = wintypes.ULONG()
    call = ctypes.WinDLL("kernel32", use_last_error=True).GetNamedPipeServerProcessId
    call.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.ULONG)]
    call.restype = wintypes.BOOL
    if not call(handle, ctypes.byref(process_id)) or process_id.value <= 0:
        raise ControlProtocolError("Guard process verification failed")
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
        raise ControlProtocolError("Process verification failed")
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            raise ControlProtocolError("Process verification failed")
        return Path(buffer.value)
    finally:
        kernel32.CloseHandle(handle)


def _same_path(actual: Path, expected: Path) -> bool:
    return os.path.normcase(os.path.abspath(actual)) == os.path.normcase(
        os.path.abspath(expected),
    )


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ControlProtocolError("Invalid policy request identifier")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ControlProtocolError("Invalid policy request identifier") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ControlProtocolError("Invalid policy request identifier")
    return value


def _digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise ControlProtocolError("Invalid policy digest")
    return value


def _encode(value: Mapping[str, object]) -> bytes:
    try:
        raw = json.dumps(
            dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ControlProtocolError("Invalid policy control message") from exc
    if len(raw) > MAX_POLICY_CONTROL_BYTES:
        raise ControlProtocolError("Policy control message is too large")
    return raw


def _decode(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes) or len(raw) > MAX_POLICY_CONTROL_BYTES:
        raise ControlProtocolError("Invalid policy control message size")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlProtocolError("Invalid policy control message") from exc
    if not isinstance(value, dict):
        raise ControlProtocolError("Invalid policy control message")
    return value


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    kind = value.get("kind")
    expected = (
        APPLY_FIELDS if kind == "apply_draft"
        else CAPABILITY_FIELDS if kind in {"activate_capability", "deactivate_capability"}
        else INITIALIZE_FIELDS if kind == "initialize_authority_state"
        else RESUME_OPERATION_FIELDS if kind in {
            "resume_lending_operation", "complete_lending_operation",
        }
        else OPERATION_FIELDS if kind == "cancel_lending_operation"
        else STATUS_FIELDS
    )
    if (
        set(value) != expected
        or value.get("policy_control_version") != POLICY_CONTROL_VERSION
        or kind not in {
            "policy_status", "apply_draft", "activate_capability", "deactivate_capability",
            "initialize_authority_state",
            "resume_lending_operation", "complete_lending_operation",
            "cancel_lending_operation",
        }
    ):
        raise ControlProtocolError("Invalid policy control request")
    _uuid(value.get("request_id"))
    if type(value.get("wallet_pid")) is not int or value["wallet_pid"] <= 0:
        raise ControlProtocolError("Invalid Wallet process")
    if kind in {
        "resume_lending_operation", "complete_lending_operation",
        "cancel_lending_operation",
    }:
        operation_id = value.get("operation_id")
        if not isinstance(operation_id, str) or not operation_id.startswith("act-"):
            raise ControlProtocolError("Invalid lending operation")
        _uuid(operation_id[4:])
        if kind in {"resume_lending_operation", "complete_lending_operation"}:
            phase_action_id = value.get("phase_action_id")
            transaction_hash = value.get("transaction_hash")
            if (
                not isinstance(phase_action_id, str)
                or not phase_action_id.startswith("act-")
                or not isinstance(transaction_hash, str)
                or not transaction_hash.startswith("0x") or len(transaction_hash) != 66
                or any(character not in "0123456789abcdef" for character in transaction_hash[2:])
                or value.get("receipt_state") != "confirmed"
            ):
                raise ControlProtocolError("Invalid lending operation receipt")
            _uuid(phase_action_id[4:])
        return dict(value)
    if kind != "policy_status":
        if (
            type(value.get("expected_policy_revision")) is not int
            or value["expected_policy_revision"] < 0
        ):
            raise ControlProtocolError("Invalid expected policy revision")
        _digest(value.get("expected_policy_digest"))
        if kind != "initialize_authority_state":
            for field in ("reviewed_draft_digest", "candidate_policy_digest"):
                _digest(value.get(field))
        if kind in {"activate_capability", "deactivate_capability"} and value.get("capability") != "lending":
            raise ControlProtocolError("Invalid policy capability")
        if kind == "initialize_authority_state" and value.get("capability") != "authority_state":
            raise ControlProtocolError("Invalid policy capability")
    return dict(value)


def validate_response(
    value: Mapping[str, object], request_id: str,
) -> dict[str, object]:
    if (
        set(value) != RESPONSE_FIELDS
        or value.get("policy_control_version") != POLICY_CONTROL_VERSION
        or value.get("kind") not in {
            "policy_status", "policy_applied", "policy_activated",
            "policy_deactivated", "policy_refused",
            "authority_initialized",
            "lending_operation_resumed", "lending_operation_completed",
            "lending_operation_cancelled",
        }
        or value.get("request_id") != request_id
        or not isinstance(value.get("code"), str)
        or not value["code"]
        or len(value["code"]) > 64
        or type(value.get("policy_revision")) is not int
        or value["policy_revision"] < 0
        or type(value.get("transfer_authority_enabled")) is not bool
        or type(value.get("lending_authority_enabled")) is not bool
        or value.get("authority_state") not in {
            "READY", "INITIALIZATION_REQUIRED", "INVALID",
        }
        or value.get("source_draft_digest") is not None
        and not isinstance(value.get("source_draft_digest"), str)
    ):
        raise ControlProtocolError("Invalid policy control response")
    _digest(value.get("policy_digest"))
    if value.get("source_draft_digest") is not None:
        _digest(value.get("source_draft_digest"))
    return dict(value)


class PolicyControlClient:
    def __init__(
        self, expected_guard_path: Path,
        pipe_name: str = POLICY_CONTROL_PIPE_NAME,
        connector: Callable[..., Connection] = Client,
        waiter: Callable[[str, float], None] = _wait_pipe,
        peer_pid: Callable[[int], int] = _server_pid,
        process_image: Callable[[int], Path] = _process_image,
        wallet_pid: Callable[[], int] = os.getpid,
    ) -> None:
        self.expected_guard_path = expected_guard_path.resolve(strict=False)
        self.pipe_name, self._connector, self._waiter = pipe_name, connector, waiter
        self._peer_pid, self._process_image, self._wallet_pid = (
            peer_pid, process_image, wallet_pid,
        )

    def status(self, timeout: float = 1.0) -> dict[str, object]:
        return self._exchange({
            "policy_control_version": POLICY_CONTROL_VERSION,
            "kind": "policy_status",
            "request_id": str(uuid.uuid4()),
            "wallet_pid": self._wallet_pid(),
        }, timeout)

    def apply(
        self, expected_policy_revision: int, expected_policy_digest: str,
        reviewed_draft_digest: str, candidate_policy_digest: str,
        timeout: float = 3.0,
    ) -> dict[str, object]:
        return self._exchange({
            "policy_control_version": POLICY_CONTROL_VERSION,
            "kind": "apply_draft",
            "request_id": str(uuid.uuid4()),
            "wallet_pid": self._wallet_pid(),
            "expected_policy_revision": expected_policy_revision,
            "expected_policy_digest": expected_policy_digest,
            "reviewed_draft_digest": reviewed_draft_digest,
            "candidate_policy_digest": candidate_policy_digest,
        }, timeout)

    def set_capability(
        self, enabled: bool, expected_policy_revision: int,
        expected_policy_digest: str, reviewed_draft_digest: str,
        candidate_policy_digest: str, timeout: float = 3.0,
    ) -> dict[str, object]:
        return self._exchange({
            "policy_control_version": POLICY_CONTROL_VERSION,
            "kind": "activate_capability" if enabled else "deactivate_capability",
            "request_id": str(uuid.uuid4()), "wallet_pid": self._wallet_pid(),
            "expected_policy_revision": expected_policy_revision,
            "expected_policy_digest": expected_policy_digest,
            "reviewed_draft_digest": reviewed_draft_digest,
            "candidate_policy_digest": candidate_policy_digest,
            "capability": "lending",
        }, timeout)

    def initialize_authority_state(
        self, expected_policy_revision: int, expected_policy_digest: str,
        timeout: float = 3.0,
    ) -> dict[str, object]:
        return self._exchange({
            "policy_control_version": POLICY_CONTROL_VERSION,
            "kind": "initialize_authority_state",
            "request_id": str(uuid.uuid4()), "wallet_pid": self._wallet_pid(),
            "expected_policy_revision": expected_policy_revision,
            "expected_policy_digest": expected_policy_digest,
            "capability": "authority_state",
        }, timeout)

    def resume_lending_operation(
        self, operation_id: str, phase_action_id: str,
        transaction_hash: str, timeout: float = 3.0,
    ) -> dict[str, object]:
        return self._exchange({
            "policy_control_version": POLICY_CONTROL_VERSION,
            "kind": "resume_lending_operation",
            "request_id": str(uuid.uuid4()), "wallet_pid": self._wallet_pid(),
            "operation_id": operation_id, "phase_action_id": phase_action_id,
            "transaction_hash": transaction_hash.lower(),
            "receipt_state": "confirmed",
        }, timeout)

    def cancel_lending_operation(
        self, operation_id: str, timeout: float = 3.0,
    ) -> dict[str, object]:
        return self._exchange({
            "policy_control_version": POLICY_CONTROL_VERSION,
            "kind": "cancel_lending_operation",
            "request_id": str(uuid.uuid4()), "wallet_pid": self._wallet_pid(),
            "operation_id": operation_id,
        }, timeout)

    def complete_lending_operation(
        self, operation_id: str, phase_action_id: str,
        transaction_hash: str, timeout: float = 3.0,
    ) -> dict[str, object]:
        return self._exchange({
            "policy_control_version": POLICY_CONTROL_VERSION,
            "kind": "complete_lending_operation",
            "request_id": str(uuid.uuid4()), "wallet_pid": self._wallet_pid(),
            "operation_id": operation_id, "phase_action_id": phase_action_id,
            "transaction_hash": transaction_hash.lower(),
            "receipt_state": "confirmed",
        }, timeout)

    def _exchange(self, request: Mapping[str, object], timeout: float) -> dict[str, object]:
        checked = validate_request(request)
        self._waiter(self.pipe_name, timeout)
        try:
            connection = self._connector(self.pipe_name, family="AF_PIPE", authkey=None)
        except Exception as exc:
            raise ControlUnavailable("Guard policy control is unavailable") from exc
        try:
            with connection:
                peer_pid = self._peer_pid(connection.fileno())
                if not _same_path(
                    self._process_image(peer_pid), self.expected_guard_path,
                ):
                    raise ControlProtocolError("Guard process verification failed")
                connection.send_bytes(_encode(checked))
                if not connection.poll(timeout):
                    raise ControlProtocolError("Guard policy response timed out")
                response = _decode(
                    connection.recv_bytes(MAX_POLICY_CONTROL_BYTES + 1),
                )
        except (ControlProtocolError, ControlUnavailable):
            raise
        except Exception as exc:
            raise ControlProtocolError("Guard policy response failed") from exc
        result = validate_response(response, str(checked["request_id"]))
        allowed = (
            {"policy_status", "policy_refused"}
            if checked["kind"] == "policy_status"
            else {
                "policy_applied", "policy_activated", "policy_deactivated",
                "authority_initialized", "policy_refused",
                "lending_operation_resumed", "lending_operation_completed",
                "lending_operation_cancelled",
            }
        )
        if result["kind"] not in allowed:
            raise ControlProtocolError("Unexpected policy control response")
        return result


class PolicyControlServer:
    def __init__(
        self, handler: Callable[[dict[str, object]], Mapping[str, object]],
        expected_wallet_path: Path,
        pipe_name: str = POLICY_CONTROL_PIPE_NAME,
        listener_factory: Callable[..., Listener] = Listener,
        peer_pid: Callable[[int], int] = _client_pid,
        process_image: Callable[[int], Path] = _process_image,
    ) -> None:
        self._handler = handler
        self.expected_wallet_path = expected_wallet_path.resolve(strict=False)
        self.pipe_name, self._listener_factory = pipe_name, listener_factory
        self._peer_pid, self._process_image = peer_pid, process_image
        self._listener: Listener | None = None
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    def start(self) -> None:
        self._listener = self._listener_factory(self.pipe_name, family="AF_PIPE", authkey=None)
        self._thread = threading.Thread(
            target=self._serve, name="holon-guard-policy-control", daemon=True,
        )
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
                    if not _same_path(
                        self._process_image(actual_pid), self.expected_wallet_path,
                    ):
                        continue
                    request = validate_request(_decode(
                        connection.recv_bytes(MAX_POLICY_CONTROL_BYTES + 1),
                    ))
                    if (
                        actual_pid != request["wallet_pid"]
                    ):
                        continue
                    response = dict(self._handler(request))
                    validate_response(response, str(request["request_id"]))
                    connection.send_bytes(_encode(response))
            except Exception:
                continue

    def stop(self) -> None:
        self._stop.set()
        listener, self._listener = self._listener, None
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
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout=1.0)
