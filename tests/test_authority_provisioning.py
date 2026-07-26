from __future__ import annotations

from types import SimpleNamespace

from holon_guard.provisioning import (
    AUTHORITY_STATE_INITIALIZATION_REQUIRED,
    AUTHORITY_STATE_INVALID,
    AUTHORITY_STATE_READY,
    AuthorityStateProvisioner,
)
from holon_guard_ipc import GuardState
from holon_journal import EventType, Journal, JournalStore
from holon_policy import Policy, PolicyRevisionStore


class Lifecycle:
    def __init__(self) -> None:
        self.snapshot = SimpleNamespace(state=GuardState.SIGNING_DISABLED)
        self.reason = ""

    def disable_signing(self, reason: str) -> None:
        self.reason = reason
        self.snapshot.state = GuardState.SIGNING_DISABLED


class Authority:
    def __init__(self) -> None:
        self.lifecycle = Lifecycle()
        self.audit = None
        self.security_failure = "JOURNAL_STATE_INVALID"


def provisioner(tmp_path):
    revisions = PolicyRevisionStore(tmp_path, Policy("2", "1", False, ()))
    return AuthorityStateProvisioner(tmp_path, revisions), revisions


def test_first_run_initialization_is_explicit_disabled_and_audited(tmp_path) -> None:
    service, revisions = provisioner(tmp_path)
    authority = Authority()
    snapshot = revisions.load()

    assert service.status() == AUTHORITY_STATE_INITIALIZATION_REQUIRED
    assert service.initialize(
        "11111111-1111-4111-8111-111111111111",
        snapshot.policy_revision,
        snapshot.policy_digest,
        authority,
    ) == "AUTHORITY_STATE_INITIALIZED"

    assert service.status() == AUTHORITY_STATE_READY
    assert all(path.is_file() for path in service.paths)
    assert not service.marker.exists()
    assert authority.security_failure is None
    assert authority.lifecycle.reason == "POLICY_AUTHORITY_DISABLED"
    assert authority.lifecycle.snapshot.state is GuardState.SIGNING_DISABLED
    assert not revisions.load().policy.authority_enabled
    assert not revisions.load().policy.lending_authority_enabled
    events = Journal(JournalStore(tmp_path / "journal.jsonl")).events()
    assert [event.event_type for event in events] == [
        EventType.AUTHORITY_STATE_INITIALIZED,
    ]
    assert events[0].code == "AUTHORITY_STATE_INITIALIZED"


def test_initialization_is_one_time_and_requires_exact_baseline(tmp_path) -> None:
    service, revisions = provisioner(tmp_path)
    authority = Authority()
    snapshot = revisions.load()

    assert service.initialize(
        "11111111-1111-4111-8111-111111111111", 1,
        snapshot.policy_digest, authority,
    ) == "AUTHORITY_STATE_BASELINE_REQUIRED"
    assert service.status() == AUTHORITY_STATE_INITIALIZATION_REQUIRED
    assert service.initialize(
        "11111111-1111-4111-8111-111111111111", 0,
        "f" * 64, authority,
    ) == "AUTHORITY_STATE_BASELINE_REQUIRED"
    assert service.initialize(
        "11111111-1111-4111-8111-111111111111", 0,
        snapshot.policy_digest, authority,
    ) == "AUTHORITY_STATE_INITIALIZED"
    assert service.initialize(
        "22222222-2222-4222-8222-222222222222", 0,
        snapshot.policy_digest, authority,
    ) == "AUTHORITY_STATE_NOT_INITIALIZABLE"


def test_partial_or_interrupted_initialization_stays_fail_closed(
    tmp_path, monkeypatch,
) -> None:
    service, revisions = provisioner(tmp_path)
    authority = Authority()
    snapshot = revisions.load()

    def fail_request_state():
        raise OSError("injected write failure")

    monkeypatch.setattr(service.request_store, "initialize_empty", fail_request_state)
    assert service.initialize(
        "11111111-1111-4111-8111-111111111111", 0,
        snapshot.policy_digest, authority,
    ) == "AUTHORITY_STATE_INITIALIZATION_FAILED"
    assert service.status() == AUTHORITY_STATE_INVALID
    assert service.marker.exists()
    assert authority.security_failure == "JOURNAL_STATE_INVALID"
    assert authority.lifecycle.snapshot.state is GuardState.SIGNING_DISABLED
    assert service.initialize(
        "22222222-2222-4222-8222-222222222222", 0,
        snapshot.policy_digest, authority,
    ) == "AUTHORITY_STATE_NOT_INITIALIZABLE"


def test_existing_single_state_file_is_not_bootstrapped(tmp_path) -> None:
    service, revisions = provisioner(tmp_path)
    service.journal_store.initialize_empty()
    snapshot = revisions.load()

    assert service.status() == AUTHORITY_STATE_INVALID
    assert service.initialize(
        "11111111-1111-4111-8111-111111111111", 0,
        snapshot.policy_digest, Authority(),
    ) == "AUTHORITY_STATE_NOT_INITIALIZABLE"


def test_orphan_journal_archive_is_prior_evidence_not_first_run(tmp_path) -> None:
    service, revisions = provisioner(tmp_path)
    service.journal_store.archive(1).write_bytes(b"")
    snapshot = revisions.load()

    assert service.status() == AUTHORITY_STATE_INVALID
    assert service.initialize(
        "11111111-1111-4111-8111-111111111111", 0,
        snapshot.policy_digest, Authority(),
    ) == "AUTHORITY_STATE_NOT_INITIALIZABLE"
