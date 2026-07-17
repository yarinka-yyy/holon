from __future__ import annotations

from pathlib import Path

from holon_contracts import SecurityCode
from holon_journal import Journal, JournalFailure, JournalStore, UnavailableJournal

from .authority_audit import AuthorityAudit
from .request_control import RequestController
from .request_model import RequestControlSnapshot
from .request_store import InvalidRequestState, MissingRequestState, RequestStateStore


def load_authority_audit(data_dir: Path) -> tuple[AuthorityAudit, str | None]:
    failure: str | None = None
    try:
        journal = Journal(JournalStore(data_dir / "journal.jsonl"))
    except JournalFailure as exc:
        journal = UnavailableJournal(exc.code)
        failure = exc.code
    request_store = RequestStateStore(data_dir / "request-control-state.json")
    try:
        request_snapshot = request_store.load()
    except (MissingRequestState, InvalidRequestState):
        request_snapshot = RequestControlSnapshot((), None, None)
        failure = failure or SecurityCode.REQUEST_CONTROL_STATE_INVALID.value
    requests = RequestController(request_store, request_snapshot)
    return AuthorityAudit(journal, requests), failure
