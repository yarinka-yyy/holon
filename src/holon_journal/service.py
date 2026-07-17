"""Single structured journal event source."""

from __future__ import annotations

from typing import Any

from .builders import EventFactory
from .model import EventType, JournalEvent
from .rules import JournalValidationError
from .store import JournalInvalid, JournalMissing, JournalStore, JournalWriteError


class JournalFailure(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Security journal is unavailable")
        self.code = code


class UnavailableJournal:
    def __init__(self, code: str = "JOURNAL_STATE_INVALID") -> None:
        self.code = code

    def emit(self, event_type: EventType, code: str, **public_fields: Any) -> JournalEvent:
        del event_type, code, public_fields
        raise JournalFailure(self.code)

    def events(self) -> tuple[JournalEvent, ...]:
        raise JournalFailure(self.code)


class Journal:
    def __init__(self, store: JournalStore, factory: EventFactory | None = None) -> None:
        self.store = store
        self.factory = factory or EventFactory()
        try:
            store.read_events()
        except (JournalMissing, JournalInvalid) as exc:
            raise JournalFailure("JOURNAL_STATE_INVALID") from exc

    def emit(self, event_type: EventType, code: str, **public_fields: Any) -> JournalEvent:
        try:
            event = self.factory.create(event_type, code, **public_fields)
            self.store.append(event)
        except (JournalValidationError, JournalWriteError, OSError) as exc:
            raise JournalFailure("JOURNAL_WRITE_FAILED") from exc
        return event

    def events(self) -> tuple[JournalEvent, ...]:
        try:
            return tuple(self.store.read_events())
        except (JournalMissing, JournalInvalid) as exc:
            raise JournalFailure("JOURNAL_STATE_INVALID") from exc
