"""Strict one-shot pipe for public Wallet balance snapshots."""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Callable, Mapping
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path

from holon_contracts.payloads import validate_wallet_balances

from .protocol import (
    ControlProtocolError,
    ControlUnavailable,
    _process_image,
    _same_path,
    _server_pid,
    _wait_pipe,
)

PUBLIC_VERSION = "1"
PUBLIC_PIPE_NAME = r"\\.\pipe\Holon.Wallet.Public.v1"
MAX_PUBLIC_BYTES = 8 * 1024
REQUEST_FIELDS = frozenset({"public_version", "kind", "query_id"})
RESPONSE_FIELDS = frozenset(
    {"public_version", "kind", "query_id", "wallet_pid", "snapshot"},
)


def _query_id(value: object) -> str:
    if not isinstance(value, str):
        raise ControlProtocolError("Invalid public query identifier")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as error:
        raise ControlProtocolError("Invalid public query identifier") from error
    if parsed.version != 4 or str(parsed) != value:
        raise ControlProtocolError("Invalid public query identifier")
    return value


def _encode(value: Mapping[str, object]) -> bytes:
    try:
        raw = json.dumps(
            dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise ControlProtocolError("Invalid public response") from error
    if len(raw) > MAX_PUBLIC_BYTES:
        raise ControlProtocolError("Public response is too large")
    return raw


def _decode(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes) or len(raw) > MAX_PUBLIC_BYTES:
        raise ControlProtocolError("Invalid public message size")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ControlProtocolError("Invalid public message") from error
    if not isinstance(value, dict):
        raise ControlProtocolError("Invalid public message")
    return value


def _request(query_id: str) -> dict[str, object]:
    return {
        "public_version": PUBLIC_VERSION,
        "kind": "read_active_balances",
        "query_id": _query_id(query_id),
    }


def _parse_request(value: Mapping[str, object]) -> str:
    if (
        set(value) != REQUEST_FIELDS
        or value.get("public_version") != PUBLIC_VERSION
        or value.get("kind") != "read_active_balances"
    ):
        raise ControlProtocolError("Invalid public request")
    return _query_id(value.get("query_id"))


def _response(
    query_id: str, wallet_pid: int, snapshot: Mapping[str, object],
) -> dict[str, object]:
    if type(wallet_pid) is not int or wallet_pid <= 0:
        raise ControlProtocolError("Invalid Wallet process")
    validate_wallet_balances(snapshot)
    return {
        "public_version": PUBLIC_VERSION,
        "kind": "active_balances",
        "query_id": _query_id(query_id),
        "wallet_pid": wallet_pid,
        "snapshot": dict(snapshot),
    }


def _parse_response(value: Mapping[str, object], query_id: str) -> tuple[int, dict]:
    if (
        set(value) != RESPONSE_FIELDS
        or value.get("public_version") != PUBLIC_VERSION
        or value.get("kind") != "active_balances"
        or value.get("query_id") != query_id
    ):
        raise ControlProtocolError("Invalid public response")
    wallet_pid = value.get("wallet_pid")
    snapshot = value.get("snapshot")
    if type(wallet_pid) is not int or wallet_pid <= 0 or not isinstance(snapshot, dict):
        raise ControlProtocolError("Invalid public response")
    validate_wallet_balances(snapshot)
    return wallet_pid, snapshot


class WalletPublicClient:
    def __init__(
        self,
        pipe_name: str = PUBLIC_PIPE_NAME,
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

    def read(
        self, query_id: str, expected_path: Path,
        readiness_timeout: float, response_timeout: float,
    ) -> dict:
        self._waiter(self.pipe_name, readiness_timeout)
        try:
            connection = self._connector(self.pipe_name, family="AF_PIPE", authkey=None)
        except Exception as error:
            raise ControlUnavailable("Public Wallet connection failed") from error
        try:
            with connection:
                peer_pid = self._peer_pid(connection.fileno())
                connection.send_bytes(_encode(_request(query_id)))
                if not connection.poll(response_timeout):
                    raise ControlProtocolError("Public Wallet response timed out")
                response = _decode(connection.recv_bytes(MAX_PUBLIC_BYTES + 1))
        except (ControlProtocolError, ControlUnavailable):
            raise
        except Exception as error:
            raise ControlProtocolError("Public Wallet response failed") from error
        wallet_pid, snapshot = _parse_response(response, query_id)
        if wallet_pid != peer_pid or not _same_path(
            self._process_image(peer_pid), expected_path,
        ):
            raise ControlProtocolError("Public Wallet process verification failed")
        return snapshot


class WalletPublicServer:
    def __init__(
        self,
        reader: Callable[[], Mapping[str, object]],
        pipe_name: str = PUBLIC_PIPE_NAME,
        listener_factory: Callable[..., Listener] = Listener,
        wallet_pid: Callable[[], int] = os.getpid,
    ) -> None:
        self._reader = reader
        self.pipe_name = pipe_name
        self._listener_factory = listener_factory
        self._wallet_pid = wallet_pid

    def serve_once(self) -> None:
        try:
            listener = self._listener_factory(
                self.pipe_name, family="AF_PIPE", authkey=None,
            )
        except Exception as error:
            raise ControlUnavailable("Public Wallet server could not start") from error
        try:
            connection = listener.accept()
            with connection:
                if not connection.poll(1.0):
                    return
                query_id = _parse_request(
                    _decode(connection.recv_bytes(MAX_PUBLIC_BYTES + 1)),
                )
                connection.send_bytes(
                    _encode(_response(query_id, self._wallet_pid(), self._reader())),
                )
        finally:
            listener.close()
