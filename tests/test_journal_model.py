from __future__ import annotations

import unittest

from holon_journal import EventFactory, EventType, parse_event
from holon_journal.codec import MAX_EVENT_BYTES, decode_event, encode_event
from holon_journal.rules import JournalValidationError

ACTION_ID = "act-22222222-2222-4222-8222-222222222222"
EVENT_ID = "11111111-1111-4111-8111-111111111111"
TIMESTAMP = "2026-07-17T12:00:00Z"


class JournalModelTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = EventFactory(clock=lambda: TIMESTAMP, id_factory=lambda: EVENT_ID)

    def test_policy_event_is_strict_and_canonical(self) -> None:
        event = self.factory.create(
            EventType.POLICY_DECISION, "POLICY_ALLOWED",
            action_id=ACTION_ID, policy_version="1", policy_result="ALLOWED",
        )
        self.assertEqual(parse_event(event.to_dict()), event)
        self.assertEqual(event.description, "Policy decision: ALLOWED.")
        self.assertLessEqual(len(encode_event(event)), MAX_EVENT_BYTES)

    def test_unknown_fields_and_noncanonical_description_are_rejected(self) -> None:
        value = self.factory.create(
            EventType.POLICY_DECISION, "POLICY_ALLOWED", policy_result="ALLOWED"
        ).to_dict()
        value["secret"] = "forbidden"
        with self.assertRaises(JournalValidationError):
            parse_event(value)
        value.pop("secret")
        value["description"] = "raw exception or input"
        with self.assertRaises(JournalValidationError):
            parse_event(value)

    def test_contract_factory_hashes_and_discards_raw_calldata(self) -> None:
        event = self.factory.contract_action(
            b"public-calldata", action_id=ACTION_ID, action_type="transfer",
            network="base", asset="usdc", amount_atomic="1000000",
            contract="0x1111111111111111111111111111111111111111",
            selector="0xa9059cbb",
        )
        serialized = encode_event(event)
        self.assertNotIn(b"public-calldata", serialized)
        self.assertEqual(len(event.public_fields["calldata_hash"]), 64)

    def test_required_fields_and_identifiers_are_enforced(self) -> None:
        with self.assertRaises(JournalValidationError):
            self.factory.create(EventType.FLOW_STARTED, "FLOW_STARTED")
        with self.assertRaises(JournalValidationError):
            self.factory.create(
                EventType.LOCAL_APPROVED, "APPROVED", action_id="act-not-a-uuid"
            )
        with self.assertRaises(JournalValidationError):
            decode_event(b"x" * (MAX_EVENT_BYTES + 1))
        value = self.factory.create(EventType.TECHNICAL_ERROR, "SAFE_ERROR").to_dict()
        value["recipient"] = "not-an-address"
        with self.assertRaises(JournalValidationError):
            parse_event(value)
