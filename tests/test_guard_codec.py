from __future__ import annotations

import unittest

from holon_guard_ipc.codec import (
    MAX_MESSAGE_BYTES,
    CodecError,
    decode_message,
    encode_message,
    make_request,
    make_response,
    validate_request,
    validate_response,
)
from holon_guard_ipc.model import GuardState


class GuardCodecTests(unittest.TestCase):
    def test_all_private_commands_have_strict_payloads(self) -> None:
        requests = (
            make_request("health"),
            make_request("start_flow", {"owner_pid": 101}),
            make_request("cancel_flow", {"flow_id": "flow"}),
            make_request("recover_flow", {"flow_id": "flow"}),
        )
        for request in requests:
            validate_request(request)
            self.assertEqual(decode_message(encode_message(request)), request)

    def test_unknown_commands_fields_and_client_flow_id_are_rejected(self) -> None:
        invalid = (
            {"ipc_version": "1", "command": "unknown", "payload": {}},
            {"ipc_version": "1", "command": "health", "payload": {}, "extra": 1},
            {"ipc_version": "1", "command": "health", "payload": {"flow_id": "x"}},
            {"ipc_version": "1", "command": "start_flow", "payload": {"owner_pid": True}},
            {"ipc_version": "1", "command": "start_flow", "payload": {"flow_id": "x"}},
        )
        for request in invalid:
            with self.assertRaises(CodecError):
                validate_request(request)

    def test_malformed_and_oversized_json_are_rejected(self) -> None:
        with self.assertRaises(CodecError):
            decode_message(b"{broken")
        with self.assertRaises(CodecError):
            decode_message(b"x" * (MAX_MESSAGE_BYTES + 1))
        with self.assertRaises(CodecError):
            encode_message({"message": "x" * MAX_MESSAGE_BYTES})

    def test_response_is_bounded_and_unknown_state_is_rejected(self) -> None:
        response = make_response(
            ok=True,
            code="OK",
            state=GuardState.NORMAL,
            message="Guard health is available.",
            flow_id=None,
        )
        validate_response(response)
        self.assertEqual(set(response), {"ok", "code", "state", "flow_id", "message"})
        for field, value in (
            ("code", ""),
            ("code", "x" * 65),
            ("message", "x" * 257),
            ("state", "UNKNOWN"),
            ("flow_id", "x" * 65),
        ):
            invalid = dict(response)
            invalid[field] = value
            with self.assertRaises(CodecError):
                validate_response(invalid)


if __name__ == "__main__":
    unittest.main()
