from __future__ import annotations

import json

import pytest
from web3 import Web3

from holon_wallet.public_data import BASE_USDC
from holon_wallet.storage import WalletPaths
from holon_wallet.storage import StorageError
from holon_wallet.trusted_recipients import (
    TrustedDraftError,
    TrustedDraftUnavailable,
    TrustedPolicyDraft,
    TrustedPolicyDraftStore,
    TrustedRecipientDraft,
    TrustedRouteDraft,
    parse_cap,
    parse_fee_cap,
    validate_draft_address,
    validate_label,
)


SENDER = Web3.to_checksum_address("0x" + "11" * 20)
RECIPIENT = Web3.to_checksum_address("0x" + "ab" * 20)
SECOND_RECIPIENT = Web3.to_checksum_address("0x" + "22" * 20)


def route(
    network: str = "base",
    asset: str = "usdc",
    recipient: str = RECIPIENT,
) -> TrustedRouteDraft:
    return TrustedRouteDraft(
        network,
        asset,
        8453 if network == "base" else 1,
        "100000000" if asset == "usdc" else "100000000000000000",
        "5000000000000000",
        (TrustedRecipientDraft("Savings", recipient, "50000000" if asset == "usdc" else "50000000000000000"),),
    )


def test_amount_fee_label_and_address_validation() -> None:
    assert parse_cap("100", "usdc") == "100000000"
    assert parse_cap("0.1", "eth") == "100000000000000000"
    assert parse_fee_cap("0.005") == "5000000000000000"
    assert validate_label("  Demo wallet  ") == "Demo wallet"
    assert validate_draft_address(RECIPIENT.lower(), SENDER) == RECIPIENT

    invalid = (
        lambda: parse_cap("0.0000001", "usdc"),
        lambda: parse_fee_cap("0"),
        lambda: validate_label(" "),
        lambda: validate_label("x" * 41),
        lambda: validate_label("bad\nlabel"),
        lambda: validate_draft_address(SENDER, SENDER),
        lambda: validate_draft_address(BASE_USDC, SENDER),
    )
    for operation in invalid:
        with pytest.raises(TrustedDraftError):
            operation()


def test_envelope_is_disabled_canonical_and_round_trips() -> None:
    draft = TrustedPolicyDraft((
        route("base", "usdc", SECOND_RECIPIENT),
        route("ethereum", "eth", RECIPIENT),
    )).canonical()
    envelope = draft.to_envelope()

    assert envelope["draft_schema_version"] == "2"
    assert envelope["policy"]["transfer_authority_enabled"] is False
    assert envelope["policy"]["lending_authority_enabled"] is False
    assert [
        (item["network"], item["asset"])
        for item in envelope["policy"]["transfer_rules"]
    ] == [("ethereum", "eth"), ("base", "usdc")]
    assert TrustedPolicyDraft.from_envelope(envelope) == draft


def test_new_lending_limits_are_withdraw_only_and_legacy_supply_round_trips() -> None:
    withdraw = TrustedPolicyDraft().with_lending_limits("1.01", "0.0001")
    rule = withdraw.to_envelope()["policy"]["lending_rules"][0]
    assert rule["allowed_actions"] == ["withdraw"]
    assert rule["max_amount_atomic"] == "1010000"
    assert rule["max_total_fee_wei"] == "100000000000000"

    legacy = TrustedPolicyDraft(
        (), "5000000", "100000000000000", ("approve", "supply"),
    )
    envelope = legacy.to_envelope()
    assert TrustedPolicyDraft.from_envelope(envelope) == legacy


def test_envelope_rejects_digest_authority_labels_and_noncanonical_order() -> None:
    envelope = TrustedPolicyDraft((route(),)).to_envelope()
    cases = []
    changed_digest = dict(envelope, policy_digest="0" * 64)
    cases.append(changed_digest)
    enabled_policy = dict(envelope["policy"], transfer_authority_enabled=True)
    cases.append(dict(envelope, policy=enabled_policy))
    cases.append(dict(envelope, recipient_labels=[]))
    changed_label = [dict(envelope["recipient_labels"][0], label=" bad\nlabel ")]
    cases.append(dict(envelope, recipient_labels=changed_label))

    for value in cases:
        with pytest.raises(TrustedDraftError):
            TrustedPolicyDraft.from_envelope(value)

    two = TrustedPolicyDraft((
        route("ethereum", "eth", RECIPIENT),
        route("base", "usdc", SECOND_RECIPIENT),
    )).to_envelope()
    reversed_policy = dict(
        two["policy"], transfer_rules=list(reversed(two["policy"]["transfer_rules"])),
    )
    from holon_policy import policy_digest
    noncanonical = dict(
        two,
        policy=reversed_policy,
        policy_digest=policy_digest(reversed_policy),
    )
    with pytest.raises(TrustedDraftError):
        TrustedPolicyDraft.from_envelope(noncanonical)


def test_route_limits_duplicates_and_empty_draft() -> None:
    draft = TrustedPolicyDraft().with_route(route())
    assert draft.route("base", "usdc") is not None
    assert draft.without_route("base", "usdc") == TrustedPolicyDraft()

    excessive = TrustedRouteDraft(
        "base", "usdc", 8453, "10", "1",
        (TrustedRecipientDraft("Too much", RECIPIENT, "11"),),
    )
    with pytest.raises(TrustedDraftError):
        TrustedPolicyDraft().with_route(excessive)
    with pytest.raises(TrustedDraftError):
        TrustedPolicyDraft().with_route(
            TrustedRouteDraft("base", "usdc", 1, "10", "1", ()),
        )


def test_store_missing_restart_corruption_and_digest_mutation(tmp_path) -> None:
    paths = WalletPaths(tmp_path)
    store = TrustedPolicyDraftStore(paths)
    assert store.load() == TrustedPolicyDraft()

    draft = TrustedPolicyDraft((route(),)).canonical()
    store.save(draft)
    assert store.load() == draft
    assert json.loads(paths.authority_policy_draft.read_text(encoding="utf-8")) == (
        draft.to_envelope()
    )

    paths.authority_policy_draft.write_text("{broken", encoding="utf-8")
    with pytest.raises(TrustedDraftUnavailable):
        store.load()

    value = draft.to_envelope()
    value["policy"]["transfer_rules"][0]["max_amount_atomic"] = "999999999"
    paths.authority_policy_draft.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TrustedDraftUnavailable):
        store.load()


def test_store_rejects_oversized_and_incompatible_draft(tmp_path) -> None:
    paths = WalletPaths(tmp_path)
    store = TrustedPolicyDraftStore(paths)
    paths.authority_policy_draft.write_bytes(b" " * (64 * 1024 + 1))
    with pytest.raises(TrustedDraftUnavailable):
        store.load()

    value = TrustedPolicyDraft().to_envelope()
    value["draft_schema_version"] = "3"
    paths.authority_policy_draft.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(TrustedDraftUnavailable):
        store.load()


def test_failed_atomic_save_preserves_previous_draft(tmp_path, monkeypatch) -> None:
    paths = WalletPaths(tmp_path)
    store = TrustedPolicyDraftStore(paths)
    original = TrustedPolicyDraft((route(),)).canonical()
    store.save(original)
    previous = paths.authority_policy_draft.read_bytes()

    def fail_write(_path, _value) -> None:
        raise StorageError("fixture write failure")

    monkeypatch.setattr(
        "holon_wallet.trusted_recipients.atomic_write_json", fail_write,
    )
    changed = TrustedPolicyDraft((route("ethereum", "eth"),)).canonical()
    with pytest.raises(TrustedDraftUnavailable):
        store.save(changed)
    assert paths.authority_policy_draft.read_bytes() == previous
