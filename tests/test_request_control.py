from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from holon_guard.request_control import (
    RequestControlFailure, RequestController, WalletRequestControl,
)
from holon_guard.request_model import (
    MAX_RECENT_ATTEMPTS, RequestAttempt, RequestControlSnapshot,
)
from holon_guard.request_store import (
    InvalidRequestState, MissingRequestState, RequestStateStore,
)
from holon_guard.semantic import semantic_fingerprint

TRANSFER = {
    "policy_version": "1", "action_type": "transfer", "network": "base",
    "asset": "usdc", "amount_atomic": "1000000",
    "recipient": "0x1111111111111111111111111111111111111111",
    "max_total_fee_wei": "500",
}


class RequestControlTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = RequestStateStore(Path(self.temporary.name) / "request-state.json")
        snapshot = self.store.bootstrap_empty_for_test()
        self.now = [100.0]
        self.controller = RequestController(self.store, snapshot, lambda: self.now[0])

    def test_semantic_hash_ignores_fee_and_case_but_not_material_fields(self) -> None:
        fee = dict(TRANSFER, max_total_fee_wei="999")
        policy = dict(TRANSFER, policy_version="999")
        case = dict(TRANSFER, recipient=TRANSFER["recipient"].upper().replace("0X", "0x"))
        self.assertEqual(semantic_fingerprint(TRANSFER), semantic_fingerprint(fee))
        self.assertEqual(semantic_fingerprint(TRANSFER), semantic_fingerprint(policy))
        self.assertEqual(semantic_fingerprint(TRANSFER), semantic_fingerprint(case))
        changed = dict(TRANSFER, amount_atomic="999999")
        self.assertNotEqual(semantic_fingerprint(TRANSFER), semantic_fingerprint(changed))
        contract = "0x2222222222222222222222222222222222222222"
        first = semantic_fingerprint(TRANSFER, contract=contract, selector="0xA9059CBB")
        second = semantic_fingerprint(TRANSFER, contract=contract, selector="0xa9059cbb")
        self.assertEqual(first, second)
        self.assertNotEqual(first, semantic_fingerprint(TRANSFER))

    def test_third_equivalent_request_starts_global_persistent_block(self) -> None:
        self.assertFalse(self.controller.observe(TRANSFER).blocked)
        self.assertFalse(self.controller.observe(dict(TRANSFER, max_total_fee_wei="600")).blocked)
        third = self.controller.observe(TRANSFER)
        self.assertTrue(third.blocked)
        self.assertTrue(third.triggered)
        other = dict(TRANSFER, amount_atomic="1")
        self.assertTrue(self.controller.observe(other).blocked)
        restarted = RequestController(self.store, self.store.load(), lambda: self.now[0])
        self.assertTrue(restarted.observe(other).blocked)

    def test_automatic_expiry_and_wallet_only_clear_reset_attempts(self) -> None:
        for _ in range(3):
            self.controller.observe(TRANSFER)
        self.now[0] = 401.0
        expired = self.controller.observe(dict(TRANSFER, amount_atomic="2"))
        self.assertTrue(expired.expired)
        self.assertFalse(expired.blocked)
        for _ in range(2):
            self.controller.observe(TRANSFER)
        self.assertTrue(self.controller.observe(TRANSFER).blocked)
        cleared: list[bool] = []
        seam = WalletRequestControl(self.controller, lambda: cleared.append(True))
        self.assertTrue(seam.clear_block())
        self.assertEqual(cleared, [True])
        self.assertFalse(self.controller.observe(TRANSFER).blocked)

    def test_clock_rollback_is_invalid_security_state(self) -> None:
        self.controller.observe(TRANSFER)
        self.controller.observe(TRANSFER)
        self.now[0] = 90.0
        with self.assertRaises(RequestControlFailure):
            self.controller.observe(TRANSFER)

    def test_capacity_clock_and_write_failures_are_normalized(self) -> None:
        attempts = tuple(
            RequestAttempt(f"{index:064x}", 100.0) for index in range(MAX_RECENT_ATTEMPTS)
        )
        full = RequestController(
            self.store, RequestControlSnapshot(attempts, None, None), lambda: 100.0
        )
        with self.assertRaises(RequestControlFailure):
            full.observe(TRANSFER)
        self.now[0] = float("nan")
        with self.assertRaises(RequestControlFailure):
            self.controller.observe(TRANSFER)
        self.now[0] = 100.0
        with patch.object(self.store, "save", side_effect=OSError("private")):
            with self.assertRaises(RequestControlFailure):
                self.controller.observe(TRANSFER)

    def test_missing_and_corrupt_state_are_not_bootstrapped(self) -> None:
        missing = RequestStateStore(Path(self.temporary.name) / "missing.json")
        with self.assertRaises(MissingRequestState):
            missing.load()
        self.store.path.write_text("{broken", encoding="utf-8")
        with self.assertRaises(InvalidRequestState):
            self.store.load()
