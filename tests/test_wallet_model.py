from holon_wallet.model import PROTOTYPE_PROFILES, ProfileSummary, WalletShellState


def test_prototype_state_starts_with_public_simulated_main_account() -> None:
    state = WalletShellState()

    assert state.active_profile_id == "main"
    assert state.active_profile == PROTOTYPE_PROFILES[0]
    assert all(profile.simulated for profile in state.profiles)
    assert all(len(profile.address) == 42 for profile in state.profiles)


def test_profile_selection_is_in_memory_and_rejects_unknown_id() -> None:
    state = WalletShellState()

    assert state.select_profile("trading")
    assert state.active_profile_id == "trading"
    assert not state.select_profile("unknown")
    assert state.active_profile_id == "trading"
    assert WalletShellState().active_profile_id == "main"


def test_profile_summary_exposes_only_short_public_address() -> None:
    profile = ProfileSummary(
        "profile", "Test Account", "0x1234567890123456789012345678901234567890",
    )

    assert profile.short_address == "0x1234...67890"


def test_state_requires_profiles_with_unique_ids() -> None:
    try:
        WalletShellState(())
    except ValueError as error:
        assert str(error) == "At least one prototype profile is required"
    else:
        raise AssertionError("Empty prototype state must fail")

    profile = PROTOTYPE_PROFILES[0]
    try:
        WalletShellState((profile, profile))
    except ValueError as error:
        assert str(error) == "Prototype profile IDs must be unique"
    else:
        raise AssertionError("Duplicate profile IDs must fail")
