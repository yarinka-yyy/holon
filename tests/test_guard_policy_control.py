from __future__ import annotations

from types import SimpleNamespace

from holon_guard.policy_control import GuardPolicyControl
from holon_guard.provisioning import AuthorityStateProvisioner
from holon_guard.__main__ import _any_authority_enabled
from holon_guard_ipc import GuardState
from holon_lending import ACTION_PROFILES_DIGEST
from holon_policy import LendingRule, Policy, PolicyRevisionStore, policy_digest
from holon_policy.baseline import BASELINE_POLICY_DIGEST
from holon_wallet.storage import WalletPaths
from holon_wallet.trusted_recipients import (
    TrustedPolicyDraft, TrustedPolicyDraftStore, TrustedRecipientDraft,
    TrustedRouteDraft, trusted_draft_digest,
)


class Lifecycle:
    def __init__(self) -> None:
        self.snapshot = SimpleNamespace(state=GuardState.SIGNING_DISABLED)
        self.ledger = SimpleNamespace(snapshot=SimpleNamespace(current=None))
        self.reason = ""

    def disable_signing(self, reason: str) -> None:
        self.reason = reason
        self.snapshot.state = GuardState.SIGNING_DISABLED

    def enable_signing(self, reason: str) -> None:
        self.reason = reason
        self.snapshot.state = GuardState.NORMAL


class Authority:
    def __init__(self) -> None:
        self.lifecycle = Lifecycle()
        self.security_failure = None
        self.snapshot = None

    def replace_policy_snapshot(self, snapshot) -> None:
        self.snapshot = snapshot


def draft() -> TrustedPolicyDraft:
    return TrustedPolicyDraft((TrustedRouteDraft(
        "base", "usdc", 8453, "100000000", "5000000000000000",
        (TrustedRecipientDraft(
            "Savings", "0x4444444444444444444444444444444444444444", "50000000",
        ),),
    ),))


def request(store: PolicyRevisionStore, draft_store: TrustedPolicyDraftStore):
    snapshot = store.load()
    envelope = draft_store.load().to_envelope()
    return {
        "kind": "apply_draft",
        "request_id": "11111111-1111-4111-8111-111111111111",
        "expected_policy_revision": snapshot.policy_revision,
        "expected_policy_digest": snapshot.policy_digest,
        "reviewed_draft_digest": trusted_draft_digest(envelope),
        "candidate_policy_digest": envelope["policy_digest"],
    }


def test_guard_reloads_exact_draft_applies_and_is_idempotent(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    revision_store = PolicyRevisionStore(tmp_path, baseline)
    draft_store = TrustedPolicyDraftStore(WalletPaths(tmp_path))
    draft_store.save(draft())
    authority = Authority()
    control = GuardPolicyControl(revision_store, draft_store, authority)

    first = control.handle(request(revision_store, draft_store))
    assert first["kind"] == "policy_applied"
    assert first["code"] == "POLICY_REVISION_APPLIED"
    assert first["policy_revision"] == 1
    assert authority.snapshot.policy_revision == 1
    assert not authority.snapshot.policy.authority_enabled
    assert authority.lifecycle.reason == "POLICY_AUTHORITY_DISABLED"

    second = control.handle(request(revision_store, draft_store))
    assert second["code"] == "POLICY_ALREADY_ACTIVE"
    assert second["policy_revision"] == 1


def test_guard_allows_only_explicit_first_run_authority_initialization(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    revision_store = PolicyRevisionStore(tmp_path, baseline)
    draft_store = TrustedPolicyDraftStore(WalletPaths(tmp_path))
    authority = Authority()
    provisioner = AuthorityStateProvisioner(tmp_path, revision_store)
    control = GuardPolicyControl(
        revision_store, draft_store, authority,
        promotion_blocker="JOURNAL_STATE_INVALID", provisioner=provisioner,
    )
    snapshot = revision_store.load()
    request_value = {
        "kind": "initialize_authority_state",
        "request_id": "11111111-1111-4111-8111-111111111111",
        "expected_policy_revision": 0,
        "expected_policy_digest": snapshot.policy_digest,
        "capability": "authority_state",
    }

    status = control.handle({"kind": "policy_status", "request_id": "status"})
    assert status["authority_state"] == "INITIALIZATION_REQUIRED"
    result = control.handle(request_value)
    assert result["kind"] == "authority_initialized"
    assert result["authority_state"] == "READY"
    assert result["transfer_authority_enabled"] is False
    assert result["lending_authority_enabled"] is False
    assert control.promotion_blocker is None
    assert control.handle(request_value)["code"] == "AUTHORITY_STATE_NOT_INITIALIZABLE"


def test_guard_restart_keeps_lending_only_authority_available() -> None:
    rule = LendingRule(
        "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
        ("approve", "supply"), "5000000", "100000000000000",
        ACTION_PROFILES_DIGEST,
    )
    assert _any_authority_enabled(Policy("3", "2", False, (), True, (rule,)))
    assert not _any_authority_enabled(Policy("2", "1", False, ()))


def test_guard_refuses_changed_stale_and_active_flow_without_replacing(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    revision_store = PolicyRevisionStore(tmp_path, baseline)
    draft_store = TrustedPolicyDraftStore(WalletPaths(tmp_path))
    draft_store.save(draft())
    authority = Authority()
    control = GuardPolicyControl(revision_store, draft_store, authority)
    original = request(revision_store, draft_store)

    changed = dict(original, reviewed_draft_digest="f" * 64)
    assert control.handle(changed)["code"] == "POLICY_DRAFT_CHANGED"
    assert revision_store.load().policy_revision == 0

    stale = dict(original, expected_policy_digest="e" * 64)
    assert control.handle(stale)["code"] == "POLICY_REVISION_STALE"
    authority.lifecycle.snapshot.state = GuardState.ACTIVE
    assert control.handle(original)["code"] == "POLICY_FLOW_ACTIVE"
    assert revision_store.load().policy_digest == BASELINE_POLICY_DIGEST


def test_guard_repairs_corrupt_active_pointer_from_reviewed_draft(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    revision_store = PolicyRevisionStore(tmp_path, baseline)
    draft_store = TrustedPolicyDraftStore(WalletPaths(tmp_path))
    draft_store.save(draft())
    authority = Authority()
    control = GuardPolicyControl(revision_store, draft_store, authority)
    first = control.handle(request(revision_store, draft_store))
    revision_store.active_path.write_text("{broken", encoding="utf-8")
    authority.security_failure = "POLICY_STATE_INVALID"
    status = control.handle({"kind": "policy_status", "request_id": "status"})
    assert status["code"] == "POLICY_REVISION_INVALID"
    envelope = draft_store.load().to_envelope()
    repair = {
        "kind": "apply_draft", "request_id": "repair",
        "expected_policy_revision": status["policy_revision"],
        "expected_policy_digest": status["policy_digest"],
        "reviewed_draft_digest": trusted_draft_digest(envelope),
        "candidate_policy_digest": envelope["policy_digest"],
    }
    result = control.handle(repair)
    assert result["kind"] == "policy_applied"
    assert result["policy_revision"] > first["policy_revision"]
    assert authority.security_failure is None


def test_lending_activation_and_deactivation_are_separate_cas_revisions(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    revision_store = PolicyRevisionStore(tmp_path, baseline)
    draft_store = TrustedPolicyDraftStore(WalletPaths(tmp_path))
    draft_store.save(draft().with_lending_limits("5000000", "100000000000000"))
    authority = Authority()
    control = GuardPolicyControl(revision_store, draft_store, authority)
    applied = control.handle(request(revision_store, draft_store))
    envelope = draft_store.load().to_envelope()

    def capability(kind: str, snapshot, candidate) -> dict[str, object]:
        return {
            "kind": kind, "request_id": kind, "capability": "lending",
            "expected_policy_revision": snapshot.policy_revision,
            "expected_policy_digest": snapshot.policy_digest,
            "reviewed_draft_digest": trusted_draft_digest(envelope),
            "candidate_policy_digest": candidate,
        }

    disabled = draft_store.load().to_policy()
    enabled = Policy("3", "2", False, disabled.transfer_rules, True, disabled.lending_rules)
    activated = control.handle(capability(
        "activate_capability", revision_store.load(),
        policy_digest(enabled.to_dict()),
    ))
    assert activated["kind"] == "policy_activated"
    assert activated["lending_authority_enabled"] is True
    assert activated["transfer_authority_enabled"] is False
    assert activated["policy_revision"] == applied["policy_revision"] + 1
    assert authority.lifecycle.snapshot.state is GuardState.NORMAL

    deactivated = control.handle(capability(
        "deactivate_capability", revision_store.load(), envelope["policy_digest"],
    ))
    assert deactivated["kind"] == "policy_deactivated"
    assert deactivated["lending_authority_enabled"] is False
    assert deactivated["transfer_authority_enabled"] is False
    assert authority.lifecycle.snapshot.state is GuardState.SIGNING_DISABLED
