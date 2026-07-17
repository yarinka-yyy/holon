from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from holon_journal import EventFactory, EventType, Journal, JournalFailure, JournalStore
from holon_journal.codec import encode_event
from holon_journal.store import JournalInvalid, JournalMissing, JournalWriteError

TIMESTAMP = "2026-07-17T12:00:00Z"


class JournalStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.store = JournalStore(Path(self.temporary.name) / "journal.jsonl")
        self.store.bootstrap_empty_for_test()
        counter = iter(range(100))
        self.factory = EventFactory(
            clock=lambda: TIMESTAMP,
            id_factory=lambda: f"00000000-0000-4000-8000-{next(counter):012d}",
        )

    def event(self, code: str = "TEST_EVENT"):
        return self.factory.create(EventType.TECHNICAL_ERROR, code)

    def test_append_order_and_restart_validation(self) -> None:
        self.store.append(self.event("FIRST"))
        self.store.append(self.event("SECOND"))
        self.assertEqual([item.code for item in self.store.read_events()], ["FIRST", "SECOND"])
        journal = Journal(self.store, self.factory)
        self.assertEqual([item.code for item in journal.events()], ["FIRST", "SECOND"])

    def test_rotation_keeps_active_plus_three_archives(self) -> None:
        segment_limit = len(encode_event(self.event("SIZE_PROBE"))) * 2 + 1
        with patch("holon_journal.store.MAX_SEGMENT_BYTES", segment_limit):
            for index in range(8):
                self.store.append(self.event(f"EVENT_{index}"))
        segments = [self.store.path] + [self.store.archive(index) for index in range(1, 4)]
        self.assertTrue(all(path.is_file() for path in segments))
        retained = self.store.read_events()
        self.assertLessEqual(len(retained), 8)
        self.assertEqual(retained[-1].code, "EVENT_7")

    def test_missing_corrupt_and_duplicate_events_are_invalid(self) -> None:
        missing = JournalStore(Path(self.temporary.name) / "missing.jsonl")
        with self.assertRaises(JournalMissing):
            missing.read_events()
        self.store.path.write_bytes(b"{broken\n")
        with self.assertRaises(JournalInvalid):
            self.store.read_events()
        raw = self.factory.create(EventType.TECHNICAL_ERROR, "DUPLICATE")
        self.store.path.write_bytes(encode_event(raw) * 2)
        with self.assertRaises(JournalInvalid):
            self.store.read_events()

    def test_write_failure_is_normalized(self) -> None:
        with patch.object(Path, "open", side_effect=OSError("private path")):
            with self.assertRaises(JournalWriteError):
                self.store.append(self.event())
        journal = Journal(self.store, self.factory)
        with patch.object(self.store, "append", side_effect=JournalWriteError("disk")):
            with self.assertRaises(JournalFailure) as raised:
                journal.emit(EventType.TECHNICAL_ERROR, "SAFE_ERROR")
        self.assertEqual(raised.exception.code, "JOURNAL_WRITE_FAILED")

    def test_unreadable_and_rotation_failures_are_fail_closed(self) -> None:
        with patch.object(Path, "read_bytes", side_effect=OSError("private")):
            with self.assertRaises(JournalInvalid):
                self.store.read_events()
        self.store.append(self.event("FIRST"))
        with patch("holon_journal.store.MAX_SEGMENT_BYTES", 1):
            with patch("holon_journal.store.os.replace", side_effect=OSError("private")):
                with self.assertRaises(JournalWriteError):
                    self.store.append(self.event("ROTATE"))
