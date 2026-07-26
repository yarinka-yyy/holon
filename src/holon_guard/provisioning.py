"""Explicit first-run initialization of persistent authority safety state."""

from __future__ import annotations

import json
import os
from pathlib import Path

from holon_journal import EventType, Journal, JournalFailure, JournalStore
from holon_policy import PolicyRevisionStore, PolicyRevisionUnavailable

from .action_store import ActionStateStore, InvalidActionState, MissingActionState
from .request_store import InvalidRequestState, MissingRequestState, RequestStateStore
from .runtime_security import load_authority_audit

AUTHORITY_STATE_READY = "READY"
AUTHORITY_STATE_INITIALIZATION_REQUIRED = "INITIALIZATION_REQUIRED"
AUTHORITY_STATE_INVALID = "INVALID"


class AuthorityStateProvisioner:
    def __init__(self, data_dir: Path, revision_store: PolicyRevisionStore) -> None:
        self.data_dir = data_dir
        self.revision_store = revision_store
        self.journal_store = JournalStore(data_dir / "journal.jsonl")
        self.action_store = ActionStateStore(data_dir / "action-state.json")
        self.request_store = RequestStateStore(data_dir / "request-control-state.json")
        self.marker = data_dir / "authority-state-initializing.json"

    @property
    def paths(self) -> tuple[Path, Path, Path]:
        return (
            self.journal_store.path, self.action_store.path, self.request_store.path,
        )

    def _orphan_evidence_exists(self) -> bool:
        archives = (
            self.journal_store.archive(index) for index in range(1, 4)
        )
        return (
            any(path.exists() for path in archives)
            or any(self.data_dir.glob(".action-state-*.tmp"))
            or any(self.data_dir.glob(".request-control-*.tmp"))
        )

    def status(self) -> str:
        if self.marker.exists() or self._orphan_evidence_exists():
            return AUTHORITY_STATE_INVALID
        present = tuple(path.exists() for path in self.paths)
        if not any(present):
            return AUTHORITY_STATE_INITIALIZATION_REQUIRED
        if not all(present):
            return AUTHORITY_STATE_INVALID
        try:
            Journal(self.journal_store)
            self.action_store.load()
            self.request_store.load()
        except (
            JournalFailure, MissingActionState, InvalidActionState,
            MissingRequestState, InvalidRequestState, OSError,
        ):
            return AUTHORITY_STATE_INVALID
        return AUTHORITY_STATE_READY

    def initialize(
        self, request_id: str, expected_revision: int, expected_digest: str,
        authority,
    ) -> str:
        if self.status() != AUTHORITY_STATE_INITIALIZATION_REQUIRED:
            return "AUTHORITY_STATE_NOT_INITIALIZABLE"
        try:
            snapshot = self.revision_store.load()
        except PolicyRevisionUnavailable:
            return "POLICY_REVISION_INVALID"
        if (
            snapshot.policy_revision != expected_revision
            or snapshot.policy_digest != expected_digest
            or snapshot.policy_revision != 0
            or snapshot.policy.authority_enabled
            or snapshot.policy.lending_authority_enabled
        ):
            return "AUTHORITY_STATE_BASELINE_REQUIRED"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        marker_value = {
            "schema_version": "1", "request_id": request_id,
            "expected_policy_revision": expected_revision,
            "expected_policy_digest": expected_digest,
        }
        try:
            with self.marker.open("x", encoding="utf-8", newline="\n") as stream:
                json.dump(marker_value, stream, separators=(",", ":"), sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            self.journal_store.initialize_empty()
            self.action_store.initialize_empty()
            self.request_store.initialize_empty()
            audit, failure = load_authority_audit(self.data_dir)
            if failure is not None:
                raise RuntimeError("Initialized authority state did not validate")
            audit.event(
                EventType.AUTHORITY_STATE_INITIALIZED,
                "AUTHORITY_STATE_INITIALIZED",
            )
            if self.status_with_marker_ignored() != AUTHORITY_STATE_READY:
                raise RuntimeError("Initialized authority state did not verify")
            self.marker.unlink()
        except (OSError, RuntimeError, JournalFailure):
            return "AUTHORITY_STATE_INITIALIZATION_FAILED"
        authority.audit = audit
        authority.security_failure = None
        authority.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        return "AUTHORITY_STATE_INITIALIZED"

    def status_with_marker_ignored(self) -> str:
        present = tuple(path.exists() for path in self.paths)
        if not all(present):
            return AUTHORITY_STATE_INVALID
        try:
            Journal(self.journal_store)
            self.action_store.load()
            self.request_store.load()
        except Exception:
            return AUTHORITY_STATE_INVALID
        return AUTHORITY_STATE_READY
