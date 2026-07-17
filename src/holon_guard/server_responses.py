"""Safe uncorrelated and contract-validation Guard responses."""

from __future__ import annotations

from holon_contracts import ContractViolation, MessageKind, SecurityCode, make_envelope
from holon_guard_ipc.codec import encode_message, make_response


def schema_mismatch(frame: object) -> bool:
    return (
        isinstance(frame, dict)
        and isinstance(frame.get("message"), dict)
        and frame["message"].get("schema_version") != "1"
    )


def generic_error(kind: MessageKind = MessageKind.ERROR) -> bytes:
    if kind is MessageKind.COMPATIBILITY_STATUS:
        payload = {
            "guard_state": "SIGNING_DISABLED",
            "authority_available": False,
            "code": SecurityCode.SCHEMA_VERSION_UNSUPPORTED.value,
            "message": "Contract schema is unsupported.",
            "supported_schema_versions": ["1"],
            "policy_version": "1",
        }
    else:
        payload = {
            "code": SecurityCode.IPC_INVALID_REQUEST.value,
            "message": "Guard rejected an invalid request.",
            "retryable": False,
        }
    return encode_message(make_response(make_envelope(kind, payload)))


def contract_failure(frame: object, violation: ContractViolation) -> bytes:
    if schema_mismatch(frame):
        return generic_error(MessageKind.COMPATIBILITY_STATUS)
    if not isinstance(frame, dict) or not isinstance(frame.get("message"), dict):
        return generic_error()
    message = frame["message"]
    response = make_envelope(
        MessageKind.REFUSAL,
        {
            "code": violation.code,
            "message": "Guard refused an invalid request.",
            "retryable": False,
        },
        request_id=message.get("request_id"),
        action_id=message.get("action_id"),
    )
    return encode_message(make_response(response))
