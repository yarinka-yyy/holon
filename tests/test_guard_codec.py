from __future__ import annotations

import unittest

from holon_contracts import MessageKind, make_envelope
from holon_guard_ipc.codec import (
    MAX_MESSAGE_BYTES, OWNER_REQUIRED_KINDS, CodecError, decode_message,
    encode_message, make_request, make_response, validate_request,
    validate_response,
)
from guard_support import ACTION_ID, transfer_request


class GuardCodecTests(unittest.TestCase):
    def test_owner_required_kind_set_is_exact_and_transport_enforced(self) -> None:
        self.assertEqual(OWNER_REQUIRED_KINDS, {
            MessageKind.PREPARE_TRANSFER,
            MessageKind.TRANSFER_INTENT,
            MessageKind.LENDING_AUTHORITY_INTENT,
        })
        requests = (
            transfer_request(),
            make_envelope(
                MessageKind.TRANSFER_INTENT,
                {
                    "network": "base", "asset": "usdc", "amount": "1",
                    "recipient": "0x1111111111111111111111111111111111111111",
                },
                action_id="act-33333333-3333-4333-8333-333333333333",
            ),
            make_envelope(
                MessageKind.LENDING_AUTHORITY_INTENT,
                {
                    "module_id": "lending", "module_version": "1",
                    "protocol_profile_id": "aave-v3-base-usdc",
                    "protocol_profile_version": "1", "network": "base",
                    "asset": "usdc", "beneficiary_mode": "active_wallet_account",
                    "action": "supply", "amount_mode": "exact", "amount": "1",
                },
                action_id="act-44444444-4444-4444-8444-444444444444",
            ),
        )
        for request in requests:
            with self.subTest(kind=request.kind.value):
                with self.assertRaises(CodecError):
                    make_request(request)
                checked, owner_pid = validate_request(make_request(request, 101))
                self.assertEqual(checked, request)
                self.assertEqual(owner_pid, 101)

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
