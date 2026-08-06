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

AUTHORITY_VERSION = "2"
AUTHORITY_PIPE_NAME = r"\\.\pipe\Holon.Wallet.Authority.v2"
MAX_AUTHORITY_BYTES = 32 * 1024
ACTION_RE = re.compile(r"^act-[0-9a-f-]{36}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,77}$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
PREPARE_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "policy_version",
    "policy_revision", "policy_digest",
    "network", "asset", "amount_atomic", "recipient", "created_at", "expires_at",
})
LENDING_PREPARE_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "policy_version",
    "policy_revision", "policy_digest", "action_profile_digest",
    "protocol_profile_id", "action",
    "amount_mode", "amount", "resolved_amount_atomic", "operation_id",
    "phase_action_id", "phase", "created_at", "expires_at",
})
MODULE_PREPARE_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "module_id",
    "capability_id", "profile_id", "action_type", "bundle", "created_at",
    "expires_at",
})
CANCEL_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "prepared_digest",
})
PREPARED_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "wallet_pid",
    "profile_id", "sender", "recipient", "network", "asset", "amount_atomic",
    "max_total_fee_wei", "prepared_digest", "created_at", "expires_at", "code",
    "policy_revision", "policy_digest", "target", "selector", "calldata_hash",
})
LENDING_PREPARED_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "wallet_pid",
    "profile_id", "sender", "requested_action", "next_action", "network",
    "asset", "amount_atomic", "target", "method", "max_total_fee_wei",
    "l2_fee_ceiling_wei", "l1_fee_upper_bound_wei", "prepared_digest",
    "created_at", "expires_at", "code", "policy_revision", "policy_digest",
    "action_profile_digest", "amount_mode",
    "operation_id", "phase_action_id", "phase",
    "selector", "calldata_hash",
})
MODULE_PREPARED_FIELDS = frozenset({
    "authority_version", "kind", "flow_id", "action_id", "wallet_pid",
    "module_id", "capability_id", "profile_id", "action_type",
    "bundle_digest", "prepared_digest", "created_at", "expires_at", "code",
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


def _module_value(value: object, *, depth: int = 0) -> None:
    if depth > 10:
        raise ControlProtocolError("Invalid module authority data")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        raise ControlProtocolError("Module authority data cannot use float")
    if isinstance(value, str):
        if len(value) > 2048 or any(ord(character) < 32 for character in value):
            raise ControlProtocolError("Invalid module authority text")
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise ControlProtocolError("Invalid module authority list")
        for item in value:
            _module_value(item, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ControlProtocolError("Invalid module authority object")
        for key, item in value.items():
            if (
                not isinstance(key, str) or not key or len(key) > 64
                or any(token in key.casefold() for token in (
                    "credential", "password", "private", "secret", "seed",
                    "signature", "signed_payload", "raw_payload",
                ))
            ):
                raise ControlProtocolError("Invalid module authority field")
            _module_value(item, depth=depth + 1)
        return
    raise ControlProtocolError("Invalid module authority data")


def validate_request(value: Mapping[str, object]) -> dict[str, object]:
    kind = value.get("kind")
    if kind not in {
        "prepare_transfer", "prepare_lending_action", "prepare_module_action",
        "cancel_transfer", "cancel_action",
    }:
        raise ControlProtocolError("Invalid authority request")
    expected = (
        PREPARE_FIELDS if kind == "prepare_transfer"
        else LENDING_PREPARE_FIELDS if kind == "prepare_lending_action"
        else MODULE_PREPARE_FIELDS if kind == "prepare_module_action"
        else CANCEL_FIELDS
    )
    legacy_lending = kind == "prepare_lending_action" and set(value) == (
        LENDING_PREPARE_FIELDS - {"protocol_profile_id"}
    )
    if (set(value) != expected and not legacy_lending) or value.get("authority_version") != AUTHORITY_VERSION:
        raise ControlProtocolError("Invalid authority request")
    _uuid(value.get("flow_id"))
    _action(value.get("action_id"))
    if kind in {"cancel_transfer", "cancel_action"}:
        digest = value.get("prepared_digest")
        if not isinstance(digest, str) or HEX_RE.fullmatch(digest) is None:
            raise ControlProtocolError("Invalid authority request")
        return dict(value)
    if kind == "prepare_module_action":
        for field in ("module_id", "capability_id"):
            if (
                not isinstance(value.get(field), str)
                or len(value[field]) > 64
                or MODULE_ID_RE.fullmatch(value[field]) is None
            ):
                raise ControlProtocolError("Invalid module authority request")
        if (
            value.get("profile_id") != "hyperliquid-mainnet-v1"
            or value.get("action_type") not in {
                "OPEN_POSITION", "CLOSE_POSITION", "HLP_DEPOSIT", "HLP_WITHDRAW",
            }
            or not isinstance(value.get("bundle"), Mapping)
        ):
            raise ControlProtocolError("Invalid module authority request")
        bundle = value["bundle"]
        _module_value(bundle)
        if (
            bundle.get("operation_id") != value.get("action_id")
            or bundle.get("profile_id") != value.get("profile_id")
            or bundle.get("action_type") != value.get("action_type")
            or bundle.get("created_at") != value.get("created_at")
            or bundle.get("expires_at") != value.get("expires_at")
            or not isinstance(bundle.get("bundle_digest"), str)
            or HEX_RE.fullmatch(bundle["bundle_digest"]) is None
        ):
            raise ControlProtocolError("Invalid module authority bundle")
        for field in ("created_at", "expires_at"):
            if not isinstance(value.get(field), str) or len(value[field]) > 40:
                raise ControlProtocolError("Invalid module authority request")
        return dict(value)
    if kind == "prepare_lending_action":
        value = {"protocol_profile_id": "aave-v3-base-usdc", **value}
        if (
            value.get("policy_version") not in {"2", "3"}
            or type(value.get("policy_revision")) is not int
            or value["policy_revision"] < 0
            or not isinstance(value.get("policy_digest"), str)
            or HEX_RE.fullmatch(value["policy_digest"]) is None
            or not isinstance(value.get("action_profile_digest"), str)
            or HEX_RE.fullmatch(value["action_profile_digest"]) is None
            or value.get("protocol_profile_id") not in {
                "aave-v3-base-usdc", "compound-v3-base-usdc",
                "morpho-v1-gauntlet-usdc-prime",
            }
            or value.get("action") not in {"supply", "withdraw"}
            or value.get("amount_mode") not in {"exact", "all"}
            or value.get("operation_id") is None
            or value.get("phase_action_id") != value.get("action_id")
            or value.get("phase") not in {"approve_or_supply", "supply", "withdraw"}
            or not isinstance(value.get("resolved_amount_atomic"), str)
            or DECIMAL_RE.fullmatch(value["resolved_amount_atomic"]) is None
            or (
                value.get("amount_mode") == "exact"
                and not isinstance(value.get("amount"), str)
            )
            or (
                value.get("amount_mode") == "all"
                and value.get("amount") is not None
            )
        ):
            raise ControlProtocolError("Invalid lending authority request")
        _action(value.get("operation_id"))
        for field in ("created_at", "expires_at"):
            if not isinstance(value.get(field), str) or len(value[field]) > 40:
                raise ControlProtocolError("Invalid authority request")
        return dict(value)
    if (
        value.get("policy_version") not in {"1", "3"}
        or type(value.get("policy_revision")) is not int
        or value["policy_revision"] < 0
        or not isinstance(value.get("policy_digest"), str)
        or HEX_RE.fullmatch(value["policy_digest"]) is None
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
    allowed_kinds = {
        "transfer_prepared", "lending_action_prepared", "transfer_refused",
        "lending_action_refused", "transfer_cancelled", "action_cancelled",
        "module_action_prepared", "module_action_refused",
    }
    expected = (
        PREPARED_FIELDS if kind == "transfer_prepared"
        else LENDING_PREPARED_FIELDS if kind == "lending_action_prepared"
        else MODULE_PREPARED_FIELDS if kind == "module_action_prepared"
        else REFUSED_FIELDS
    )
    if (
        set(value) != expected or kind not in allowed_kinds
        or value.get("authority_version") != AUTHORITY_VERSION
        or value.get("flow_id") != request.get("flow_id")
        or value.get("action_id") != request.get("action_id")
        or value.get("wallet_pid") != peer_pid
        or not isinstance(value.get("code"), str)
        or CODE_RE.fullmatch(value["code"]) is None
    ):
        raise ControlProtocolError("Invalid authority response")
    if kind not in {"transfer_prepared", "lending_action_prepared", "module_action_prepared"}:
        return dict(value)
    if kind == "module_action_prepared":
        for field in (
            "module_id", "capability_id", "profile_id", "action_type",
            "created_at", "expires_at",
        ):
            if value.get(field) != request.get(field):
                raise ControlProtocolError("Module authority response mismatch")
        bundle = request.get("bundle")
        if (
            not isinstance(bundle, Mapping)
            or value.get("bundle_digest") != bundle.get("bundle_digest")
            or not isinstance(value.get("prepared_digest"), str)
            or HEX_RE.fullmatch(value["prepared_digest"]) is None
        ):
            raise ControlProtocolError("Invalid module authority response")
        return dict(value)
    if kind == "lending_action_prepared":
        for field in (
            "requested_action", "created_at", "expires_at", "policy_revision",
            "policy_digest", "action_profile_digest", "amount_mode",
            "operation_id", "phase_action_id", "phase",
        ):
            expected_field = "action" if field == "requested_action" else field
            if value.get(field) != request.get(expected_field):
                raise ControlProtocolError("Authority response mismatch")
        if (
            value.get("next_action") not in {
                "approve", "supply", "deposit", "withdraw", "redeem",
            }
            or value.get("network") != "base" or value.get("asset") != "usdc"
            or value.get("method") != value.get("next_action")
            or (
                request.get("action") == "withdraw"
                and value.get("next_action") not in {"withdraw", "redeem"}
            )
            or (
                request.get("action") == "supply"
                and value.get("next_action") not in {"approve", "supply", "deposit"}
            )
            or not isinstance(value.get("target"), str)
            or ADDRESS_RE.fullmatch(value["target"]) is None
            or not isinstance(value.get("selector"), str)
            or not value["selector"].startswith("0x") or len(value["selector"]) != 10
            or any(character not in "0123456789abcdef" for character in value["selector"][2:])
            or not isinstance(value.get("calldata_hash"), str)
            or HEX_RE.fullmatch(value["calldata_hash"]) is None
        ):
            raise ControlProtocolError("Invalid lending authority response")
        for field in (
            "amount_atomic", "max_total_fee_wei", "l2_fee_ceiling_wei",
            "l1_fee_upper_bound_wei",
        ):
            if not isinstance(value.get(field), str) or DECIMAL_RE.fullmatch(value[field]) is None:
                raise ControlProtocolError("Invalid lending authority response")
        if (
            not isinstance(value.get("profile_id"), str) or not value["profile_id"]
            or not isinstance(value.get("sender"), str)
            or ADDRESS_RE.fullmatch(value["sender"]) is None
            or not isinstance(value.get("prepared_digest"), str)
            or HEX_RE.fullmatch(value["prepared_digest"]) is None
        ):
            raise ControlProtocolError("Invalid lending authority response")
        return dict(value)
    for field in (
        "network", "asset", "amount_atomic", "recipient", "created_at", "expires_at",
        "policy_revision", "policy_digest",
    ):
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
        or not isinstance(value.get("target"), str)
        or ADDRESS_RE.fullmatch(value["target"]) is None
        or value.get("selector") is not None and (
            not isinstance(value["selector"], str)
            or not value["selector"].startswith("0x") or len(value["selector"]) != 10
            or any(character not in "0123456789abcdef" for character in value["selector"][2:])
        )
        or not isinstance(value.get("calldata_hash"), str)
        or HEX_RE.fullmatch(value["calldata_hash"]) is None
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
