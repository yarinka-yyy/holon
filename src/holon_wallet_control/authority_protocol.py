"""Strict Guard-to-Wallet transfer preparation and cancellation channel."""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from collections.abc import Callable, Mapping
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path

from .protocol import (
    ControlProtocolError, ControlUnavailable, _process_image, _same_path,
    _server_pid, _wait_pipe,
)

AUTHORITY_VERSION = "1"
AUTHORITY_PIPE_NAME = r"\\.\pipe\Holon.Wallet.Authority.v1"
MAX_AUTHORITY_BYTES = 8 * 1024
ACTION_RE = re.compile(r"^act-[0-9a-f-]{36}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,77}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
PREPARE_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "policy_version",
    "network", "asset", "amount_atomic", "recipient", "created_at", "expires_at",
})
CANCEL_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "prepared_digest",
})
PREPARED_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "wallet_pid",
    "profile_id", "sender", "recipient", "network", "asset", "amount_atomic",
    "max_total_fee_wei", "prepared_digest", "created_at", "expires_at", "code",
})
REFUSED_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "wallet_pid", "code",
})


def _uuid(value: object) -> str:
    if not isinstance(value, str):
        raise ControlProtocolError("Invalid authority identifier")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ControlProtocolError("Invalid authority identifier") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ControlProtocolError("Invalid authority identifier")
    return value


def _action(value: object) -> str:
    if not isinstance(value, str) or ACTION_RE.fullmatch(value) is None:
        raise ControlProtocolError("Invalid authority action")
    _uuid(value[4:])
    return value


def _encode(value: Mapping[str, object]) -> bytes:
    try:
        raw = json.dumps(dict(value), separators=(",", ":"), sort_keys=True).encode()
    except (TypeError, ValueError) as error:
        raise ControlProtocolError("Invalid authority message") from error
    if len(raw) > MAX_AUTHORITY_BYTES:
        raise ControlProtocolError("Authority message is too large")
    return raw


def _decode(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes) or len(raw) > MAX_AUTHORITY_BYTES:
        raise ControlProtocolError("Invalid authority message size")
    try:
        value = json.loads(raw.decode())
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControlProtocolError("Invalid authority message") from error
    if not isinstance(value, dict):
        raise ControlProtocolError("Invalid authority message")
    return value


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    kind = value.get("kind")
    expected = PREPARE_FIELDS if kind == "prepare_transfer" else CANCEL_FIELDS
    if set(value) != expected or value.get("authority_version") != AUTHORITY_VERSION:
        raise ControlProtocolError("Invalid authority request")
    _uuid(value.get("flow_id"))
    _action(value.get("action_id"))
    if kind == "cancel_transfer":
        digest = value.get("prepared_digest")
        if not isinstance(digest, str) or HEX_RE.fullmatch(digest) is None:
            raise ControlProtocolError("Invalid authority request")
        return dict(value)
    if (
        value.get("policy_version") != "1"
        or value.get("network") not in {"ethereum", "base"}
        or value.get("asset") not in {"eth", "usdc"}
        or not isinstance(value.get("amount_atomic"), str)
        or DECIMAL_RE.fullmatch(value["amount_atomic"]) is None
        or not isinstance(value.get("recipient"), str)
        or ADDRESS_RE.fullmatch(value["recipient"]) is None
    ):
        raise ControlProtocolError("Invalid authority request")
    for field in ("created_at", "expires_at"):
        if not isinstance(value.get(field), str) or len(value[field]) > 40:
            raise ControlProtocolError("Invalid authority request")
    return dict(value)


def validate_response(
    value: Mapping[str, object], request: Mapping[str, object], peer_pid: int,
) -> dict[str, object]:
    kind = value.get("kind")
    expected = PREPARED_FIELDS if kind == "transfer_prepared" else REFUSED_FIELDS
    if (
        set(value) != expected
        or value.get("authority_version") != AUTHORITY_VERSION
        or value.get("flow_id") != request.get("flow_id")
        or value.get("action_id") != request.get("action_id")
        or value.get("wallet_pid") != peer_pid
        or not isinstance(value.get("code"), str)
        or CODE_RE.fullmatch(value["code"]) is None
    ):
        raise ControlProtocolError("Invalid authority response")
    if kind != "transfer_prepared":
        return dict(value)
    for field in ("network", "asset", "amount_atomic", "recipient", "created_at", "expires_at"):
        if value.get(field) != request.get(field):
            raise ControlProtocolError("Authority response mismatch")
    if (
        not isinstance(value.get("profile_id"), str)
        or not value["profile_id"]
        or not isinstance(value.get("sender"), str)
        or ADDRESS_RE.fullmatch(value["sender"]) is None
        or not isinstance(value.get("max_total_fee_wei"), str)
        or DECIMAL_RE.fullmatch(value["max_total_fee_wei"]) is None
        or not isinstance(value.get("prepared_digest"), str)
        or HEX_RE.fullmatch(value["prepared_digest"]) is None
    ):
        raise ControlProtocolError("Invalid authority response")
    return dict(value)


class WalletAuthorityClient:
    def __init__(
        self, pipe_name: str = AUTHORITY_PIPE_NAME,
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

    def exchange(
        self, request: Mapping[str, object], expected_path: Path,
        readiness_timeout: float, response_timeout: float | None = None,
    ) -> dict[str, object]:
        checked = validate_request(request)
        self._waiter(self.pipe_name, readiness_timeout)
        try:
            connection = self._connector(self.pipe_name, family="AF_PIPE", authkey=None)
        except Exception as error:
            raise ControlUnavailable("Wallet authority connection failed") from error
        try:
            with connection:
                peer_pid = self._peer_pid(connection.fileno())
                connection.send_bytes(_encode(checked))
                if not connection.poll(
                    readiness_timeout if response_timeout is None else response_timeout
                ):
                    raise ControlProtocolError("Wallet authority response timed out")
                response = _decode(connection.recv_bytes(MAX_AUTHORITY_BYTES + 1))
        except (ControlProtocolError, ControlUnavailable):
            raise
        except Exception as error:
            raise ControlProtocolError("Wallet authority response failed") from error
        if not _same_path(self._process_image(peer_pid), expected_path):
            raise ControlProtocolError("Wallet process verification failed")
        return validate_response(response, checked, peer_pid)


class WalletAuthorityServer:
    def __init__(
        self, handler: Callable[[dict[str, object]], Mapping[str, object]],
        pipe_name: str = AUTHORITY_PIPE_NAME,
        listener_factory: Callable[..., Listener] = Listener,
        wallet_pid: Callable[[], int] = os.getpid,
    ) -> None:
        self._handler = handler
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
            self._listener = self._listener_factory(self.pipe_name, family="AF_PIPE", authkey=None)
        except Exception as error:
            raise ControlUnavailable("Wallet authority server could not start") from error
        self._thread = threading.Thread(target=self._serve, name="holon-wallet-authority", daemon=True)
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
                    request = validate_request(_decode(connection.recv_bytes(MAX_AUTHORITY_BYTES + 1)))
                    response = dict(self._handler(request))
                    response["wallet_pid"] = self._wallet_pid()
                    validate_response(response, request, response["wallet_pid"])
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
