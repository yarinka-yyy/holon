from __future__ import annotations

import json

import pytest

from holon_policy import (
    LendingRule, Policy, PolicyRevisionStale, PolicyRevisionStore, PolicyRevisionUnavailable,
    RecipientRule, TransferRule, policy_digest,
)
from holon_lending import ACTION_PROFILES_DIGEST
from holon_policy.baseline import BASELINE_POLICY_DIGEST


def disabled_policy(amount: str = "1000000") -> Policy:
    return Policy("2", "1", False, (
        TransferRule(
            "base", "usdc", 8453, amount, "1000000000000000",
            (RecipientRule("0x" + "ab" * 20, amount),),
        ),
    ))


def test_two_slot_apply_restart_idempotence_and_empty_policy(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    store = PolicyRevisionStore(tmp_path, baseline)
    initial = store.load()
    assert initial.policy_revision == 0
    assert initial.policy_digest == BASELINE_POLICY_DIGEST

    first, changed = store.apply(
        disabled_policy(), "1" * 64, 0, BASELINE_POLICY_DIGEST,
    )
    assert changed and first.policy_revision == 1
    pointer = json.loads(store.active_path.read_text(encoding="utf-8"))
    assert pointer["active_slot"] == "a"
    assert PolicyRevisionStore(tmp_path, baseline).load() == first

    same, changed = store.apply(
        disabled_policy(), "2" * 64, 1, first.policy_digest,
    )
    assert not changed and same == first

    empty, changed = store.apply(
        baseline, "3" * 64, 1, first.policy_digest,
    )
    assert changed and empty.policy_revision == 2
    assert empty.policy.transfer_rules == ()
    pointer = json.loads(store.active_path.read_text(encoding="utf-8"))
    assert pointer["active_slot"] == "b"


def test_first_empty_draft_creates_revision_one(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    store = PolicyRevisionStore(tmp_path, baseline)
    snapshot, changed = store.apply(
        baseline, "1" * 64, 0, BASELINE_POLICY_DIGEST,
    )
    assert changed and snapshot.policy_revision == 1
    assert snapshot.policy.transfer_rules == ()


def test_stale_corruption_inactive_slot_and_repair(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    store = PolicyRevisionStore(tmp_path, baseline)
    first, _ = store.apply(disabled_policy(), "1" * 64, 0, BASELINE_POLICY_DIGEST)
    with pytest.raises(PolicyRevisionStale):
        store.apply(disabled_policy("2"), "2" * 64, 0, BASELINE_POLICY_DIGEST)

    store.slot_path("b").write_text("{broken", encoding="utf-8")
    assert store.load() == first

    store.slot_path("a").write_text("{broken", encoding="utf-8")
    with pytest.raises(PolicyRevisionUnavailable):
        store.load()
    recovery = store.recoverable_snapshot()
    assert recovery.policy_revision == 0
    repaired, changed = store.apply(
        disabled_policy("2"), "3" * 64,
        recovery.policy_revision, recovery.policy_digest, repair=True,
    )
    assert changed and repaired.policy_revision == 1
    assert store.load() == repaired


def test_pointer_write_failure_preserves_previous_active_revision(
    tmp_path, monkeypatch,
) -> None:
    baseline = Policy("2", "1", False, ())
    store = PolicyRevisionStore(tmp_path, baseline)
    first, _ = store.apply(disabled_policy(), "1" * 64, 0, BASELINE_POLICY_DIGEST)
    previous = store.active_path.read_bytes()
    original_write = store._write

    def fail_pointer(path, value) -> None:
        if path == store.active_path:
            raise OSError("fixture pointer failure")
        original_write(path, value)

    monkeypatch.setattr(store, "_write", fail_pointer)
    with pytest.raises(PolicyRevisionUnavailable):
        store.apply(
            disabled_policy("2"), "2" * 64,
            first.policy_revision, first.policy_digest,
        )
    assert store.active_path.read_bytes() == previous
    assert PolicyRevisionStore(tmp_path, baseline).load() == first


def test_revision_rejects_enabled_policy_and_digest_mutation(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    store = PolicyRevisionStore(tmp_path, baseline)
    enabled = Policy("2", "1", True, disabled_policy().transfer_rules)
    with pytest.raises(PolicyRevisionUnavailable):
        store.apply(enabled, "1" * 64, 0, BASELINE_POLICY_DIGEST)

    snapshot, _ = store.apply(disabled_policy(), "2" * 64, 0, BASELINE_POLICY_DIGEST)
    pointer = json.loads(store.active_path.read_text(encoding="utf-8"))
    slot = store.slot_path(pointer["active_slot"])
    value = json.loads(slot.read_text(encoding="utf-8"))
    value["policy"]["transfer_rules"][0]["max_amount_atomic"] = "2"
    value["policy_digest"] = policy_digest(value["policy"])
    slot.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(PolicyRevisionUnavailable):
        store.load()
    assert snapshot.policy_revision == 1


def test_v3_lending_activation_is_explicit_and_legacy_baseline_stays_disabled(tmp_path) -> None:
    baseline = Policy("2", "1", False, ())
    store = PolicyRevisionStore(tmp_path, baseline)
    rule = LendingRule(
        "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
        ("approve", "supply"), "5000000", "100000000000000",
        ACTION_PROFILES_DIGEST,
    )
    disabled = Policy("3", "2", False, (), False, (rule,))
    saved, changed = store.apply(disabled, "4" * 64, 0, BASELINE_POLICY_DIGEST)
    assert changed and not saved.policy.lending_authority_enabled
    enabled = Policy("3", "2", False, (), True, (rule,))
    active, changed = store.apply(
        enabled, "4" * 64, saved.policy_revision, saved.policy_digest,
        require_disabled=False,
    )
    assert changed and active.policy.lending_authority_enabled
    assert not active.policy.authority_enabled
    restored, changed = store.apply(
        disabled, "4" * 64, active.policy_revision, active.policy_digest,
        require_disabled=False,
    )
    assert changed and not restored.policy.lending_authority_enabled


def test_active_v3_revision_migrates_atomically_to_transfer_only_v4(tmp_path) -> None:
    baseline = Policy.transfer_only_v4(Policy("2", "1", False, ()), enabled=False)
    store = PolicyRevisionStore(tmp_path, baseline)
    rule = LendingRule(
        "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
        ("approve", "supply"), "5000000", "100000000000000",
        ACTION_PROFILES_DIGEST,
    )
    legacy = Policy("3", "2", False, disabled_policy().transfer_rules, True, (rule,))
    saved, _ = store.apply(
        legacy, "7" * 64, 0, store.load().policy_digest,
        require_disabled=False,
    )

    migrated, changed = store.migrate_to_v4()

    assert changed and migrated.policy_revision == saved.policy_revision + 1
    assert migrated.policy.schema_version == "4"
    assert migrated.policy.transfer_rules == legacy.transfer_rules
    assert not migrated.policy.authority_enabled
    assert not migrated.policy.lending_authority_enabled
    assert migrated.policy.lending_rules == ()
    assert migrated.source_draft_digest == "7" * 64
