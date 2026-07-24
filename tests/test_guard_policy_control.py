from __future__ import annotations

from types import SimpleNamespace

from holon_guard.policy_control import GuardPolicyControl
from holon_guard_ipc import GuardState
from holon_policy import Policy, PolicyRevisionStore
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
