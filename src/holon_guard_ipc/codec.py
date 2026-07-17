"""Bounded transport frame carrying shared contract envelopes."""

from __future__ import annotations

import json
from typing import Any, Mapping

from holon_contracts import ContractEnvelope, ContractViolation, MessageKind, parse_envelope
from holon_contracts.schemas import REQUEST_KINDS

IPC_VERSION = "1"
MAX_MESSAGE_BYTES = 8 * 1024
PIPE_NAME = r"\\.\pipe\Holon.Guard.v1"
REQUEST_FIELDS = frozenset({"ipc_version", "message", "owner_pid"})
RESPONSE_FIELDS = frozenset({"ipc_version", "message"})


class CodecError(ValueError):
    pass


def encode_message(message: Mapping[str, Any]) -> bytes:
    try:
        raw = json.dumps(
            dict(message), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise CodecError("Message is not JSON serializable") from exc
    if len(raw) > MAX_MESSAGE_BYTES:
        raise CodecError("Message exceeds the IPC size limit")
    return raw


def decode_message(raw: bytes) -> dict[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > MAX_MESSAGE_BYTES:
        raise CodecError("Invalid IPC message size")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CodecError("Invalid IPC JSON") from exc
    if not isinstance(value, dict):
        raise CodecError("IPC message must be an object")
    return value


def make_request(envelope: ContractEnvelope, owner_pid: int | None = None) -> dict[str, Any]:
    request = {
        "ipc_version": IPC_VERSION,
        "message": envelope.to_dict(),
        "owner_pid": owner_pid,
    }
    validate_request(request)
    return request


def validate_request(request: Mapping[str, Any]) -> tuple[ContractEnvelope, int | None]:
    if set(request) != REQUEST_FIELDS:
        raise CodecError("Invalid IPC request frame")
    if request.get("ipc_version") != IPC_VERSION:
        raise CodecError("Unsupported IPC version")
    try:
        envelope = parse_envelope(request.get("message"))
    except ContractViolation:
        raise
    except (TypeError, ValueError) as exc:
        raise CodecError("Invalid contract envelope") from exc
    if envelope.kind not in REQUEST_KINDS:
        raise CodecError("Contract message is not a request")
    owner_pid = request.get("owner_pid")
    requires_owner = envelope.kind is MessageKind.PREPARE_TRANSFER
    if requires_owner and (type(owner_pid) is not int or owner_pid <= 0):
        raise CodecError("Invalid owner PID")
    if not requires_owner and owner_pid is not None:
        raise CodecError("Unexpected owner PID")
    return envelope, owner_pid


def make_response(envelope: ContractEnvelope) -> dict[str, Any]:
    response = {"ipc_version": IPC_VERSION, "message": envelope.to_dict()}
    validate_response(response)
    return response


def validate_response(response: Mapping[str, Any]) -> ContractEnvelope:
    if set(response) != RESPONSE_FIELDS or response.get("ipc_version") != IPC_VERSION:
        raise CodecError("Invalid IPC response frame")
    try:
        envelope = parse_envelope(response.get("message"))
    except (TypeError, ValueError) as exc:
        raise CodecError("Invalid contract response") from exc
    if envelope.kind in REQUEST_KINDS:
        raise CodecError("Contract message is not a response")
    return envelope
