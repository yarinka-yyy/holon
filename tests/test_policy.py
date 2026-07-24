from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from holon_contracts import RefusalCode
from holon_policy import Policy, PolicyEngine, PolicyLoadError, load_policy, policy_digest
from holon_policy.baseline import BASELINE_POLICY_DIGEST, load_baseline_policy

TRANSFER = {
    "policy_version": "1", "action_type": "transfer", "network": "base",
    "asset": "usdc", "amount_atomic": "1000000",
    "recipient": "0x1111111111111111111111111111111111111111",
    "max_total_fee_wei": "500",
}


def policy_value(enabled: bool = True) -> dict:
    return {
        "schema_version": "2",
        "policy_version": "1",
        "authority_enabled": enabled,
        "transfer_rules": [
            {
                "network": "base", "asset": "usdc", "chain_id": 8453,
                "max_amount_atomic": "1000000", "max_total_fee_wei": "500",
                "recipients": [{
                    "address": TRANSFER["recipient"],
                    "max_amount_atomic": "750000",
                }],
            }
        ] if enabled else [],
    }


class PolicyTests(unittest.TestCase):
    def test_production_baseline_is_pinned_and_authority_disabled(self) -> None:
        policy = load_baseline_policy()
        self.assertFalse(policy.authority_enabled)
        self.assertEqual(policy.transfer_rules, ())
        self.assertEqual(policy_digest(policy.to_dict()), BASELINE_POLICY_DIGEST)
        decision = PolicyEngine(policy).evaluate_transfer(TRANSFER)
        self.assertEqual(decision.code, RefusalCode.POLICY_AUTHORITY_DISABLED.value)

    def test_enabled_policy_allows_only_bounded_network_asset_amount_and_fee(self) -> None:
        engine = PolicyEngine(Policy.from_dict(policy_value()))
        allowed = dict(TRANSFER, amount_atomic="750000")
        self.assertTrue(engine.evaluate_transfer(allowed).allowed)
        cases = (
            ("action_type", "revoke", RefusalCode.ACTION_NOT_ALLOWED.value),
            ("network", "ethereum", RefusalCode.NETWORK_NOT_ALLOWED.value),
            ("asset", "eth", RefusalCode.ASSET_NOT_ALLOWED.value),
            ("recipient", "0x2222222222222222222222222222222222222222", RefusalCode.RECIPIENT_NOT_ALLOWED.value),
            ("amount_atomic", "750001", RefusalCode.AMOUNT_LIMIT_EXCEEDED.value),
            ("max_total_fee_wei", "501", RefusalCode.MAX_FEE_EXCEEDED.value),
            ("policy_version", "2", RefusalCode.POLICY_VERSION_MISMATCH.value),
        )
        for field, value, code in cases:
            with self.subTest(field=field):
                payload = dict(allowed)
                payload[field] = value
                self.assertEqual(engine.evaluate_transfer(payload).code, code)

    def test_missing_corrupt_changed_and_incompatible_policy_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "policy.json"
            with self.assertRaises(PolicyLoadError) as missing:
                load_policy(path, "0" * 64)
            self.assertEqual(missing.exception.code, "POLICY_STATE_INVALID")
            path.write_text("{broken", encoding="utf-8")
            with self.assertRaises(PolicyLoadError):
                load_policy(path, "0" * 64)
            value = policy_value()
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(PolicyLoadError) as changed:
                load_policy(path, "0" * 64)
            self.assertEqual(changed.exception.code, "POLICY_INTEGRITY_FAILED")
            self.assertEqual(load_policy(path, policy_digest(value)), Policy.from_dict(value))
            incompatible = dict(value, schema_version="3")
            path.write_text(json.dumps(incompatible), encoding="utf-8")
            with self.assertRaises(PolicyLoadError) as rejected:
                load_policy(path, policy_digest(incompatible))
            self.assertEqual(rejected.exception.code, "POLICY_STATE_INVALID")

    def test_recipient_rules_are_strict_bounded_and_unique(self) -> None:
        value = policy_value()
        rule = value["transfer_rules"][0]
        invalid_values = (
            dict(rule, recipients=[]),
            dict(rule, recipients=[{"address": "not-an-address", "max_amount_atomic": "1"}]),
            dict(rule, recipients=[
                {"address": TRANSFER["recipient"], "max_amount_atomic": "1"},
                {"address": TRANSFER["recipient"].upper().replace("0X", "0x"), "max_amount_atomic": "1"},
            ]),
            dict(rule, recipients=[{
                "address": TRANSFER["recipient"], "max_amount_atomic": "1000001",
            }]),
        )
        for candidate in invalid_values:
            with self.subTest(candidate=candidate):
                changed = dict(value, transfer_rules=[candidate])
                with self.assertRaises(ValueError):
                    Policy.from_dict(changed)


if __name__ == "__main__":
    unittest.main()
