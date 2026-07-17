"""Bounded JSON codec for the private M2.02 Guard transport."""

from __future__ import annotations

import json
from typing import Any, Mapping

from .model import GuardState

IPC_VERSION = "1"
MAX_MESSAGE_BYTES = 8 * 1024
PIPE_NAME = r"\\.\pipe\Holon.Guard.v1"
COMMANDS = frozenset({"health", "start_flow", "cancel_flow", "recover_flow"})
TOP_LEVEL_FIELDS = frozenset({"ipc_version", "command", "payload"})
RESPONSE_FIELDS = frozenset({"ok", "code", "state", "flow_id", "message"})


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


def make_request(command: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
    request = {"ipc_version": IPC_VERSION, "command": command, "payload": dict(payload or {})}
    validate_request(request)
    return request


def validate_request(request: Mapping[str, Any]) -> None:
    if set(request) != TOP_LEVEL_FIELDS or request.get("ipc_version") != IPC_VERSION:
        raise CodecError("Invalid IPC request envelope")
    command = request.get("command")
    payload = request.get("payload")
    if command not in COMMANDS or not isinstance(payload, dict):
        raise CodecError("Invalid IPC command")
    expected = {
        "health": set(),
        "start_flow": {"owner_pid"},
        "cancel_flow": {"flow_id"},
        "recover_flow": {"flow_id"},
    }[command]
    if set(payload) != expected:
        raise CodecError("Invalid IPC command payload")
    if command == "start_flow":
        owner_pid = payload["owner_pid"]
        if type(owner_pid) is not int or owner_pid <= 0:
            raise CodecError("Invalid owner PID")
    elif command in {"cancel_flow", "recover_flow"}:
        flow_id = payload["flow_id"]
        if not isinstance(flow_id, str) or not flow_id or len(flow_id) > 64:
            raise CodecError("Invalid flow ID")


def make_response(
    *, ok: bool, code: str, state: GuardState, message: str, flow_id: str | None
) -> dict[str, Any]:
    return {
        "ok": bool(ok),
        "code": code,
        "state": state.value,
        "flow_id": flow_id,
        "message": message,
    }


def validate_response(response: Mapping[str, Any]) -> None:
    if set(response) != RESPONSE_FIELDS:
        raise CodecError("Invalid IPC response envelope")
    if type(response.get("ok")) is not bool:
        raise CodecError("Invalid IPC response status")
    code = response.get("code")
    message = response.get("message")
    if (
        not isinstance(code, str)
        or not code
        or len(code) > 64
        or not isinstance(message, str)
        or len(message) > 256
    ):
        raise CodecError("Invalid IPC response text")
    try:
        state = GuardState(response.get("state"))
    except (TypeError, ValueError) as exc:
        raise CodecError("Invalid IPC response state") from exc
    if state is GuardState.UNKNOWN:
        raise CodecError("Unknown Guard state is not a valid response")
    flow_id = response.get("flow_id")
    if flow_id is not None and (not isinstance(flow_id, str) or len(flow_id) > 64):
        raise CodecError("Invalid IPC response flow ID")
