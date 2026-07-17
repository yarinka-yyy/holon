from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from holon_guard.request_store import RequestStateStore
from holon_guard.runtime_security import load_authority_audit
from holon_journal import JournalStore


class RuntimeSecurityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def test_missing_journal_has_priority_and_is_not_bootstrapped(self) -> None:
        audit, failure = load_authority_audit(self.root)
        self.assertEqual(failure, "JOURNAL_STATE_INVALID")
        self.assertFalse((self.root / "journal.jsonl").exists())
        self.assertFalse((self.root / "request-control-state.json").exists())
        self.assertIsNotNone(audit)

    def test_missing_or_corrupt_request_state_is_fail_closed(self) -> None:
        JournalStore(self.root / "journal.jsonl").bootstrap_empty_for_test()
        _, missing = load_authority_audit(self.root)
        self.assertEqual(missing, "REQUEST_CONTROL_STATE_INVALID")
        path = self.root / "request-control-state.json"
        path.write_text("{broken", encoding="utf-8")
        _, corrupt = load_authority_audit(self.root)
        self.assertEqual(corrupt, "REQUEST_CONTROL_STATE_INVALID")

    def test_valid_test_bootstrap_loads_without_failure(self) -> None:
        JournalStore(self.root / "journal.jsonl").bootstrap_empty_for_test()
        RequestStateStore(
            self.root / "request-control-state.json"
        ).bootstrap_empty_for_test()
        _, failure = load_authority_audit(self.root)
        self.assertIsNone(failure)


if __name__ == "__main__":
    unittest.main()
