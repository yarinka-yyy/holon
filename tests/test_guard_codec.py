from __future__ import annotations

import unittest

from holon_contracts import MessageKind, make_envelope
from holon_guard_ipc.codec import (
    MAX_MESSAGE_BYTES, CodecError, decode_message, encode_message, make_request,
    make_response, validate_request, validate_response,
)
from guard_support import ACTION_ID, transfer_request


class GuardCodecTests(unittest.TestCase):
    def test_health_and_prepare_frames_keep_owner_pid_transport_only(self) -> None:
        health = make_envelope(MessageKind.HEALTH_REQUEST, {})
        health_frame = make_request(health)
        parsed_health, owner = validate_request(health_frame)
        self.assertEqual(parsed_health, health)
        self.assertIsNone(owner)

        prepare = transfer_request()
        prepare_frame = make_request(prepare, 101)
        parsed_prepare, owner = validate_request(prepare_frame)
        self.assertEqual(parsed_prepare, prepare)
        self.assertEqual(owner, 101)
        self.assertNotIn("owner_pid", prepare.to_dict())

    def test_old_m202_shape_and_wrong_owner_fields_are_rejected(self) -> None:
        legacy = {"ipc_version": "1", "command": "health", "payload": {}}
        with self.assertRaises(CodecError):
            validate_request(legacy)
        with self.assertRaises(CodecError):
            make_request(transfer_request(), None)
        health = make_envelope(MessageKind.HEALTH_REQUEST, {})
        with self.assertRaises(CodecError):
            make_request(health, 101)

    def test_response_frame_contains_only_version_and_contract_message(self) -> None:
        response = make_envelope(
            MessageKind.REFUSAL,
            {"code": "ACTION_REPLAYED", "message": "Action was refused.", "retryable": False},
            action_id=ACTION_ID,
        )
        frame = make_response(response)
        self.assertEqual(set(frame), {"ipc_version", "message"})
        self.assertEqual(validate_response(frame), response)

    def test_malformed_and_oversized_json_are_rejected(self) -> None:
        with self.assertRaises(CodecError):
            decode_message(b"{broken")
        with self.assertRaises(CodecError):
            decode_message(b"x" * (MAX_MESSAGE_BYTES + 1))
        with self.assertRaises(CodecError):
            encode_message({"message": "x" * MAX_MESSAGE_BYTES})

    def test_response_request_kind_and_unknown_fields_are_rejected(self) -> None:
        request = make_envelope(MessageKind.HEALTH_REQUEST, {})
        with self.assertRaises(CodecError):
            validate_response({"ipc_version": "1", "message": request.to_dict()})
        response = make_envelope(
            MessageKind.ERROR,
            {"code": "IPC_INVALID_REQUEST", "message": "Invalid request.", "retryable": False},
        )
        frame = make_response(response)
        frame["extra"] = True
        with self.assertRaises(CodecError):
            validate_response(frame)


if __name__ == "__main__":
    unittest.main()
