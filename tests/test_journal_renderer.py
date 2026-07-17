from __future__ import annotations

import unittest

from holon_journal import EventFactory, EventType, render_journal

ACTION_ID = "act-22222222-2222-4222-8222-222222222222"


class JournalRendererTests(unittest.TestCase):
    def test_wallet_rows_are_newest_first_and_public_only(self) -> None:
        identifiers = iter(
            (
                "11111111-1111-4111-8111-111111111111",
                "22222222-2222-4222-8222-222222222222",
            )
        )
        factory = EventFactory(
            clock=lambda: "2026-07-17T12:00:00Z", id_factory=lambda: next(identifiers)
        )
        policy = factory.create(
            EventType.POLICY_DECISION, "POLICY_ALLOWED", policy_result="ALLOWED"
        )
        contract = factory.contract_action(
            b"canary-calldata", action_id=ACTION_ID, action_type="transfer",
            network="base", asset="usdc", amount_atomic="1000000",
            contract="0x1111111111111111111111111111111111111111",
            selector="0xa9059cbb",
        )
        rows = render_journal((policy, contract))
        self.assertIn("contract=0x1111", rows[0])
        self.assertIn("calldata_hash=", rows[0])
        self.assertIn("Policy decision: ALLOWED", rows[1])
        self.assertNotIn("canary-calldata", "".join(rows))

    def test_lifecycle_block_and_recovery_use_fixed_templates(self) -> None:
        factory = EventFactory(clock=lambda: "2026-07-17T12:00:00Z")
        common = {
            "action_id": ACTION_ID, "guard_state": "RECOVERY_REQUIRED",
        }
        events = (
            factory.create(
                EventType.FLOW_STARTED, "FLOW_STARTED", action_id=ACTION_ID,
                flow_id="11111111-1111-4111-8111-111111111111", guard_state="ACTIVE",
            ),
            factory.create(
                EventType.REQUEST_BLOCK_STARTED, "REQUEST_TEMPORARILY_BLOCKED", **common
            ),
            factory.create(EventType.RECOVERY_REQUIRED, "WALLET_INTERRUPTED", **common),
        )
        rendered = render_journal(events)
        self.assertIn("requires recovery", rendered[0])
        self.assertIn("temporarily blocked", rendered[1])
        self.assertIn("flow started", rendered[2])
