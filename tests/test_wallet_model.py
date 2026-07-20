from __future__ import annotations

from holon_wallet.model import ProfileSummary, WalletShellState
from holon_wallet.wallet_crypto import DERIVATION_PATH, MNEMONIC_PROFILE


def profile(profile_id: str, label: str) -> ProfileSummary:
    return ProfileSummary(
        profile_id,
        label,
        "0x" + profile_id[-1] * 40,
        MNEMONIC_PROFILE,
        DERIVATION_PATH,
        "2026-07-20T00:00:00Z",
    )


def test_empty_state_has_no_active_profile() -> None:
    state = WalletShellState()

    assert state.profiles == ()
    assert state.active_profile_id is None
    assert state.active_profile is None
    assert not state.select_profile("unknown")


def test_state_selects_and_replaces_public_profiles() -> None:
    first = profile("00000000-0000-4000-8000-000000000001", "Main Account")
    second = profile("00000000-0000-4000-8000-000000000002", "Account 2")
    state = WalletShellState((first, second), second.profile_id)

    assert state.active_profile == second
    assert state.select_profile(first.profile_id)
    assert not state.select_profile("unknown")
    state.replace_profiles((first, second), "unknown")
    assert state.active_profile == first


def test_profile_summary_exposes_short_public_address() -> None:
    item = profile("00000000-0000-4000-8000-000000000003", "Account")

    assert item.short_address == "0x3333...33333"


def test_state_rejects_duplicate_profile_ids() -> None:
    item = profile("00000000-0000-4000-8000-000000000004", "Account")
    try:
        WalletShellState((item, item))
    except ValueError as error:
        assert str(error) == "Profile IDs must be unique"
    else:
        raise AssertionError("Duplicate profile IDs must fail")
