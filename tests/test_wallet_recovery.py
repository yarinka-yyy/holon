from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from holon_wallet.controller import _recovery_value
from holon_wallet.model import ProfileSummary
from holon_wallet.recovery import (
    RecoveryActionError,
    RecoveryFlowCoordinator,
    RecoveryFlowState,
    RecoveryMaterialKind,
)
from holon_wallet.vault import VaultRepository
from holon_wallet.storage import WalletPaths
from holon_wallet.wallet_crypto import (
    DERIVATION_PATH,
    MNEMONIC_PROFILE,
    RAW_KEY_PROFILE,
    generate_mnemonic,
    import_private_key,
)


def summary(profile_type: str = MNEMONIC_PROFILE) -> ProfileSummary:
    return ProfileSummary(
        profile_id="profile-1",
        label="Main Account",
        address="0xA36a4C1DB7B78DaAb8CFc31D73E9aa437eE4F82a",
        profile_type=profile_type,
        derivation_path=DERIVATION_PATH if profile_type == MNEMONIC_PROFILE else None,
        created_at="2026-07-22T00:00:00Z",
    )


def test_recovery_action_is_exact_immutable_and_single_use() -> None:
    now = datetime(2026, 7, 22, tzinfo=UTC)
    coordinator = RecoveryFlowCoordinator(
        clock=lambda: now,
        action_id_factory=lambda: "recovery-fixed",
    )
    profile = summary()
    action = coordinator.prepare(profile, RecoveryMaterialKind.PRIVATE_KEY)

    assert coordinator.state is RecoveryFlowState.PREPARED
    assert action.derivation_path == DERIVATION_PATH
    assert action.permission == "reveal_and_copy_once"
    assert action.material_fields()["material_kind"] == "private_key"
    with pytest.raises(AttributeError):
        action.address = "changed"  # type: ignore[misc]
    with pytest.raises(RecoveryActionError):
        coordinator.preflight(action.action_id, "0" * 64, profile)
    assert coordinator.state is RecoveryFlowState.LOCKED
    with pytest.raises(RecoveryActionError):
        coordinator.preflight(action.action_id, action.digest, profile)


def test_recovery_expiry_profile_binding_and_supported_material_matrix() -> None:
    current = [datetime(2026, 7, 22, tzinfo=UTC)]
    coordinator = RecoveryFlowCoordinator(
        clock=lambda: current[0],
        action_id_factory=lambda: "recovery-expiry",
    )
    profile = summary()
    action = coordinator.prepare(profile, RecoveryMaterialKind.SEED_PHRASE)
    current[0] += timedelta(minutes=5)
    with pytest.raises(RecoveryActionError, match="expired"):
        coordinator.preflight(action.action_id, action.digest, profile)

    raw = summary(RAW_KEY_PROFILE)
    with pytest.raises(RecoveryActionError):
        RecoveryFlowCoordinator().prepare(raw, RecoveryMaterialKind.SEED_PHRASE)
    raw_action = RecoveryFlowCoordinator().prepare(
        raw, RecoveryMaterialKind.PRIVATE_KEY,
    )
    assert raw_action.derivation_path is None

    changed = replace(profile, address="0x0000000000000000000000000000000000000001")
    changed_coordinator = RecoveryFlowCoordinator()
    changed_action = changed_coordinator.prepare(
        profile, RecoveryMaterialKind.SEED_PHRASE,
    )
    with pytest.raises(RecoveryActionError, match="changed"):
        changed_coordinator.preflight(
            changed_action.action_id, changed_action.digest, changed,
        )


def test_authenticated_material_formats_seed_and_both_private_key_types(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    mnemonic = generate_mnemonic()
    mnemonic_record = repository.new_record(mnemonic, "Main Account")
    seed_action = RecoveryFlowCoordinator().prepare(
        mnemonic_record.summary, RecoveryMaterialKind.SEED_PHRASE,
    )
    key_action = RecoveryFlowCoordinator().prepare(
        mnemonic_record.summary, RecoveryMaterialKind.PRIVATE_KEY,
    )

    seed_value = _recovery_value(mnemonic_record, seed_action)
    mnemonic_key = _recovery_value(mnemonic_record, key_action)
    assert len(seed_value.split()) == 12
    assert mnemonic_key.startswith("0x") and len(mnemonic_key) == 66
    assert all(character in "0123456789abcdef" for character in mnemonic_key[2:])

    raw_record = repository.new_record(import_private_key("11" * 32), "Account 2")
    raw_action = RecoveryFlowCoordinator().prepare(
        raw_record.summary, RecoveryMaterialKind.PRIVATE_KEY,
    )
    raw_value = _recovery_value(raw_record, raw_action)
    assert raw_value == "0x" + "11" * 32
    assert mnemonic.value not in repr(mnemonic_record)
    assert raw_value not in repr(raw_record)
