"""Validated append-only JSONL journal with bounded rotation."""

from __future__ import annotations

import os
from pathlib import Path

from .codec import decode_event, encode_event
from .model import JournalEvent
from .rules import JournalValidationError

MAX_SEGMENT_BYTES = 1024 * 1024
SEGMENT_COUNT = 4


class JournalMissing(FileNotFoundError):
    pass


class JournalInvalid(ValueError):
    pass


class JournalWriteError(OSError):
    pass


class JournalStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def archive(self, index: int) -> Path:
        return self.path.with_name(f"{self.path.stem}.{index}{self.path.suffix}")

    def _existing_segments(self) -> list[Path]:
        archives = [self.archive(index) for index in range(SEGMENT_COUNT - 1, 0, -1)]
        present = [path for path in archives if path.exists()]
        indexes = [index for index in range(1, SEGMENT_COUNT) if self.archive(index).exists()]
        if indexes and indexes != list(range(1, max(indexes) + 1)):
            raise JournalInvalid("Journal rotation is incomplete")
        return present + [self.path]

    @staticmethod
    def _read_segment(path: Path) -> list[JournalEvent]:
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise JournalInvalid("Journal segment is unreadable") from exc
        if len(raw) > MAX_SEGMENT_BYTES or (raw and not raw.endswith(b"\n")):
            raise JournalInvalid("Journal segment is invalid")
        try:
            return [decode_event(line + b"\n") for line in raw.splitlines()]
        except JournalValidationError as exc:
            raise JournalInvalid("Journal event is invalid") from exc

    def read_events(self) -> list[JournalEvent]:
        if not self.path.is_file():
            raise JournalMissing("Journal is missing")
        events: list[JournalEvent] = []
        for path in self._existing_segments():
            events.extend(self._read_segment(path))
        identifiers = [event.event_id for event in events]
        if len(set(identifiers)) != len(identifiers):
            raise JournalInvalid("Journal contains duplicate event identifiers")
        return events

    def _rotate(self) -> None:
        try:
            oldest = self.archive(SEGMENT_COUNT - 1)
            if oldest.exists():
                oldest.unlink()
            for index in range(SEGMENT_COUNT - 2, 0, -1):
                source = self.archive(index)
                if source.exists():
                    os.replace(source, self.archive(index + 1))
            os.replace(self.path, self.archive(1))
            self.path.write_bytes(b"")
        except OSError as exc:
            raise JournalWriteError("Journal rotation failed") from exc

    def append(self, event: JournalEvent) -> None:
        raw = encode_event(event)
        try:
            current_size = self.path.stat().st_size
        except OSError as exc:
            raise JournalWriteError("Journal is unavailable") from exc
        if current_size + len(raw) > MAX_SEGMENT_BYTES:
            self._rotate()
        try:
            with self.path.open("ab") as stream:
                if stream.write(raw) != len(raw):
                    raise OSError("short journal write")
                stream.flush()
                os.fsync(stream.fileno())
        except OSError as exc:
            raise JournalWriteError("Journal append failed") from exc

    def bootstrap_empty_for_test(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_bytes(b"")
