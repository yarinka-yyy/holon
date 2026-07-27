from pathlib import Path


def test_wallet_build_bundles_both_lending_profile_files() -> None:
    script = (
        Path(__file__).parents[1] / "packaging" / "build-wallet.ps1"
    ).read_text(encoding="utf-8")

    assert 'holon_lending\\read-profiles.json' in script
    assert '--add-data "$lendingReadProfile;holon_lending"' in script
    assert 'holon_lending\\action-profiles.json' in script
    assert '--add-data "$lendingActionProfile;holon_lending"' in script
