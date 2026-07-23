from __future__ import annotations

import unittest

from holon_contracts import (
    ContractViolation, MessageKind, RefusalCode, SecurityCode, make_envelope,
    parse_envelope,
)

REQUEST_ID = "req-11111111-1111-4111-8111-111111111111"
ACTION_ID = "act-22222222-2222-4222-8222-222222222222"
TIMESTAMP = "2026-07-17T12:00:00Z"
TRANSFER = {
    "policy_version": "1",
    "action_type": "transfer",
    "network": "base",
    "asset": "usdc",
    "amount_atomic": "1000000",
    "recipient": "0x1111111111111111111111111111111111111111",
    "max_total_fee_wei": "50000000000000",
}


class ContractTests(unittest.TestCase):
    def envelope(self, payload: dict | None = None) -> dict:
        return {
            "schema_version": "1",
            "request_id": REQUEST_ID,
            "kind": "prepare_transfer",
            "timestamp": TIMESTAMP,
            "payload": dict(TRANSFER if payload is None else payload),
            "action_id": ACTION_ID,
        }

    def assert_code(self, value: dict, code: str) -> None:
        with self.assertRaises(ContractViolation) as raised:
            parse_envelope(value)
        self.assertEqual(raised.exception.code, code)

    def test_all_request_kinds_are_strict_and_versioned(self) -> None:
        transfer = parse_envelope(self.envelope())
        self.assertEqual(transfer.kind, MessageKind.PREPARE_TRANSFER)
        for kind in (
            MessageKind.ACTION_STATUS_REQUEST,
            MessageKind.CANCEL_ACTION,
            MessageKind.RECOVER_ACTION,
        ):
            message = make_envelope(
                kind, {}, request_id=REQUEST_ID, action_id=ACTION_ID,
                timestamp=TIMESTAMP,
            )
            self.assertEqual(parse_envelope(message.to_dict()), message)
        health = make_envelope(
            MessageKind.HEALTH_REQUEST, {}, request_id=REQUEST_ID, timestamp=TIMESTAMP
        )
        self.assertNotIn("action_id", health.to_dict())
        open_wallet = make_envelope(
            MessageKind.OPEN_WALLET, {}, request_id=REQUEST_ID, timestamp=TIMESTAMP
        )
        self.assertEqual(parse_envelope(open_wallet.to_dict()), open_wallet)
        self.assertNotIn("action_id", open_wallet.to_dict())
        balances = make_envelope(
            MessageKind.READ_WALLET_BALANCES, {}, request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )
        self.assertEqual(parse_envelope(balances.to_dict()), balances)
        self.assertNotIn("action_id", balances.to_dict())

    def test_open_wallet_and_wallet_opened_are_strict_and_safe(self) -> None:
        request = make_envelope(
            MessageKind.OPEN_WALLET, {}, request_id=REQUEST_ID, timestamp=TIMESTAMP,
        )
        invalid = request.to_dict()
        invalid["payload"] = {"wallet_path": "private"}
        self.assert_code(invalid, RefusalCode.REQUEST_INVALID.value)
        invalid = request.to_dict()
        invalid["action_id"] = ACTION_ID
        self.assert_code(invalid, RefusalCode.REQUEST_INVALID.value)
        response = make_envelope(
            MessageKind.WALLET_OPENED,
            {
                "guard_state": "SIGNING_DISABLED",
                "authority_available": False,
                "wallet_state": "ACTIVATED",
                "code": "WALLET_ACTIVATED",
                "message": "Wallet is open.",
            },
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )
        self.assertEqual(parse_envelope(response.to_dict()), response)
        for field in ("pid", "wallet_path", "pipe_name", "launch_id"):
            unsafe = response.to_dict()
            unsafe["payload"] = dict(response.payload, **{field: "hidden"})
            self.assert_code(unsafe, RefusalCode.REQUEST_INVALID.value)

    def test_wallet_balances_are_strict_nested_and_public_only(self) -> None:
        assets = {
            "ETH": {
                "asset": "ETH", "amount_atomic": "1000000000000000000",
                "decimals": 18, "display": "1 ETH",
            },
            "USDC": {
                "asset": "USDC", "amount_atomic": "2500000",
                "decimals": 6, "display": "2.5 USDC",
            },
        }
        response = make_envelope(
            MessageKind.WALLET_BALANCES,
            {
                "status": "READY",
                "authority_available": False,
                "account": {
                    "label": "Account 1",
                    "address": "0x1111111111111111111111111111111111111111",
                },
                "networks": [
                    {
                        "network": "ethereum", "chain_id": 1, "status": "LIVE",
                        "block_number": "123", "updated_at": TIMESTAMP,
                        "error_code": None, "balances": assets,
                    },
                    {
                        "network": "base", "chain_id": 8453, "status": "LIVE",
                        "block_number": "456", "updated_at": TIMESTAMP,
                        "error_code": None, "balances": assets,
                    },
                ],
                "code": "BALANCES_READY",
                "message": "Wallet balances are available.",
            },
            request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        )
        self.assertEqual(parse_envelope(response.to_dict()), response)
        for mutation in (
            lambda value: value["payload"].update({"ciphertext": "secret"}),
            lambda value: value["payload"]["networks"].reverse(),
            lambda value: value["payload"]["networks"][0]["balances"]["ETH"].update(
                {"amount_atomic": "1.0"},
            ),
            lambda value: value["payload"].update({"status": "PARTIAL"}),
        ):
            invalid = response.to_dict()
            mutation(invalid)
            self.assert_code(invalid, RefusalCode.REQUEST_INVALID.value)

        request = make_envelope(
            MessageKind.READ_WALLET_BALANCES, {}, request_id=REQUEST_ID,
            timestamp=TIMESTAMP,
        ).to_dict()
        request["payload"] = {"address": "0x" + "1" * 40}
        self.assert_code(request, RefusalCode.REQUEST_INVALID.value)

    def test_unknown_and_arbitrary_authority_fields_are_distinct(self) -> None:
        unknown = dict(TRANSFER, surprise="x")
        self.assert_code(self.envelope(unknown), RefusalCode.UNKNOWN_AUTHORITY_FIELD.value)
        arbitrary = dict(TRANSFER, calldata="0xdeadbeef")
        self.assert_code(self.envelope(arbitrary), RefusalCode.ARBITRARY_CALL_REFUSED.value)

    def test_amount_fee_recipient_and_action_id_are_bounded(self) -> None:
        for field, value, code in (
            ("amount_atomic", "0", RefusalCode.AMOUNT_INVALID.value),
            ("amount_atomic", "01", RefusalCode.AMOUNT_INVALID.value),
            ("amount_atomic", "1.0", RefusalCode.AMOUNT_INVALID.value),
            ("amount_atomic", "1" * 79, RefusalCode.AMOUNT_INVALID.value),
            ("max_total_fee_wei", "0", RefusalCode.MAX_FEE_REQUIRED.value),
            ("recipient", "0x1234", RefusalCode.REQUEST_INVALID.value),
        ):
            payload = dict(TRANSFER)
            payload[field] = value
            self.assert_code(self.envelope(payload), code)
        missing_fee = dict(TRANSFER)
        missing_fee.pop("max_total_fee_wei")
        self.assert_code(self.envelope(missing_fee), RefusalCode.MAX_FEE_REQUIRED.value)
        invalid_id = self.envelope()
        invalid_id["action_id"] = "act-not-a-uuid"
        self.assert_code(invalid_id, RefusalCode.ACTION_ID_INVALID.value)

    def test_schema_timestamp_and_envelope_fields_fail_closed(self) -> None:
        unsupported = self.envelope()
        unsupported["schema_version"] = "2"
        self.assert_code(unsupported, SecurityCode.SCHEMA_VERSION_UNSUPPORTED.value)
        invalid_time = self.envelope()
        invalid_time["timestamp"] = "yesterday"
        self.assert_code(invalid_time, RefusalCode.REQUEST_INVALID.value)
        non_rfc3339 = self.envelope()
        non_rfc3339["timestamp"] = "2026-07-17 12:00:00Z"
        self.assert_code(non_rfc3339, RefusalCode.REQUEST_INVALID.value)
        invalid_request = self.envelope()
        invalid_request["request_id"] = "req-not-a-uuid"
        self.assert_code(invalid_request, RefusalCode.REQUEST_INVALID.value)
        extra = self.envelope()
        extra["owner_pid"] = 7
        self.assert_code(extra, RefusalCode.REQUEST_INVALID.value)

    def test_response_payloads_reject_unsafe_types_and_unknown_fields(self) -> None:
        cases = (
            (MessageKind.ERROR, {"code": "ERROR", "message": "safe", "retryable": "no"}),
            (MessageKind.HEALTH_RESPONSE, {
                "guard_state": "NORMAL", "authority_available": False, "code": "OK",
                "message": "safe", "compatibility": "UNKNOWN",
            }),
        )
        for kind, payload in cases:
            with self.subTest(kind=kind.value), self.assertRaises(ContractViolation):
                make_envelope(kind, payload)


if __name__ == "__main__":
    unittest.main()
