"""One-shot non-authoritative Lending preview pipe between Guard and Wallet."""

from __future__ import annotations

import json
import os
import re
import sys
import uuid
from collections.abc import Callable, Mapping
from multiprocessing.connection import Client, Connection, Listener
from pathlib import Path

from holon_contracts.payloads import (
    validate_lending_action_intent,
    validate_lending_action_preview,
)

from .protocol import (
    ControlProtocolError, ControlUnavailable, _client_pid, _process_image,
    _same_path, _server_pid, _wait_pipe,
)

LENDING_PREVIEW_VERSION = "1"
LENDING_PREVIEW_PIPE_NAME = r"\\.\pipe\Holon.Wallet.LendingPreview.v1"
MAX_LENDING_PREVIEW_BYTES = 8 * 1024
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
REQUEST_FIELDS = frozenset({
    "preview_version", "kind", "correlation_id", "profile_digest", "intent",
})
RESPONSE_FIELDS = frozenset({
    "preview_version", "kind", "correlation_id", "wallet_pid", "preview",
})


def _correlation(value: object) -> str:
    if not isinstance(value, str):
        raise ControlProtocolError("Invalid preview correlation")
    try:
        parsed = uuid.UUID(value)
    except (ValueError, AttributeError) as exc:
        raise ControlProtocolError("Invalid preview correlation") from exc
    if parsed.version != 4 or str(parsed) != value:
        raise ControlProtocolError("Invalid preview correlation")
    return value


def _encode(value: Mapping[str, object]) -> bytes:
    try:
        raw = json.dumps(
            dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ControlProtocolError("Invalid preview message") from exc
    if len(raw) > MAX_LENDING_PREVIEW_BYTES:
        raise ControlProtocolError("Preview message is too large")
    return raw


def _decode(raw: bytes) -> dict[str, object]:
    if not isinstance(raw, bytes) or len(raw) > MAX_LENDING_PREVIEW_BYTES:
        raise ControlProtocolError("Invalid preview message size")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ControlProtocolError("Invalid preview message") from exc
    if not isinstance(value, dict):
        raise ControlProtocolError("Invalid preview message")
    return value


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    if (
        set(value) != REQUEST_FIELDS
        or value.get("preview_version") != LENDING_PREVIEW_VERSION
        or value.get("kind") != "prepare_lending_preview"
    ):
        raise ControlProtocolError("Invalid preview request")
    _correlation(value.get("correlation_id"))
    digest, intent = value.get("profile_digest"), value.get("intent")
    if not isinstance(digest, str) or HEX_RE.fullmatch(digest) is None or not isinstance(intent, dict):
        raise ControlProtocolError("Invalid preview request")
    try:
        validate_lending_action_intent(intent)
    except Exception as exc:
        raise ControlProtocolError("Invalid preview request") from exc
    return dict(value)


def validate_response(
    value: Mapping[str, object], request: Mapping[str, object], peer_pid: int,
) -> dict[str, object]:
    if (
        set(value) != RESPONSE_FIELDS
        or value.get("preview_version") != LENDING_PREVIEW_VERSION
        or value.get("kind") != "lending_preview"
        or value.get("correlation_id") != request.get("correlation_id")
        or value.get("wallet_pid") != peer_pid
        or not isinstance(value.get("preview"), dict)
    ):
        raise ControlProtocolError("Invalid preview response")
    try:
        validate_lending_action_preview(value["preview"])
    except Exception as exc:
        raise ControlProtocolError("Invalid preview response") from exc
    return dict(value)


class WalletLendingPreviewClient:
    def __init__(
        self, pipe_name: str = LENDING_PREVIEW_PIPE_NAME,
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

    def prepare(
        self, request: Mapping[str, object], expected_path: Path,
        readiness_timeout: float, response_timeout: float,
    ) -> dict[str, object]:
        checked = validate_request(request)
        self._waiter(self.pipe_name, readiness_timeout)
        try:
            connection = self._connector(self.pipe_name, family="AF_PIPE", authkey=None)
        except Exception as exc:
            raise ControlUnavailable("Wallet preview connection failed") from exc
        try:
            with connection:
                peer_pid = self._peer_pid(connection.fileno())
                connection.send_bytes(_encode(checked))
                if not connection.poll(response_timeout):
                    raise ControlProtocolError("Wallet preview response timed out")
                response = _decode(connection.recv_bytes(MAX_LENDING_PREVIEW_BYTES + 1))
        except (ControlProtocolError, ControlUnavailable):
            raise
        except Exception as exc:
            raise ControlProtocolError("Wallet preview response failed") from exc
        if not _same_path(self._process_image(peer_pid), expected_path):
            raise ControlProtocolError("Wallet process verification failed")
        return validate_response(response, checked, peer_pid)


class WalletLendingPreviewServer:
    def __init__(
        self, handler: Callable[[dict[str, object]], Mapping[str, object]],
        pipe_name: str = LENDING_PREVIEW_PIPE_NAME,
        listener_factory: Callable[..., Listener] = Listener,
        wallet_pid: Callable[[], int] = os.getpid,
        peer_pid: Callable[[int], int] = _client_pid,
        process_image: Callable[[int], Path] = _process_image,
        expected_guard_path: Path | None = None,
    ) -> None:
        self._handler = handler
        self.pipe_name = pipe_name
        self._listener_factory = listener_factory
        self._wallet_pid = wallet_pid
        self._peer_pid = peer_pid
        self._process_image = process_image
        self._expected_guard_path = (
            expected_guard_path.resolve(strict=False)
            if expected_guard_path is not None
            else (
                Path(sys.executable).resolve(strict=False).with_name("HolonGuard.exe")
                if getattr(sys, "frozen", False)
                else Path(sys.executable).resolve(strict=False)
            )
        )

    def serve_once(self) -> None:
        try:
            listener = self._listener_factory(self.pipe_name, family="AF_PIPE", authkey=None)
        except Exception as exc:
            raise ControlUnavailable("Wallet preview server could not start") from exc
        try:
            connection = listener.accept()
            with connection:
                peer_pid = self._peer_pid(connection.fileno())
                if not _same_path(
                    self._process_image(peer_pid), self._expected_guard_path,
                ):
                    raise ControlProtocolError("Guard process verification failed")
                if not connection.poll(1.0):
                    return
                request = validate_request(
                    _decode(connection.recv_bytes(MAX_LENDING_PREVIEW_BYTES + 1)),
                )
                preview = dict(self._handler(request))
                validate_lending_action_preview(preview)
                response = {
                    "preview_version": LENDING_PREVIEW_VERSION,
                    "kind": "lending_preview",
                    "correlation_id": request["correlation_id"],
                    "wallet_pid": self._wallet_pid(),
                    "preview": preview,
                }
                validate_response(response, request, response["wallet_pid"])
                connection.send_bytes(_encode(response))
        finally:
            listener.close()
