from holon_wallet.controller import WalletController


def test_controller_exposes_public_simulated_profile_maps() -> None:
    controller = WalletController()

    assert controller.currentScreen == "main"
    assert controller.activeProfileId == "main"
    assert controller.activeProfile["label"] == "Main Account"
    assert [profile["id"] for profile in controller.profiles] == [
        "main", "trading", "savings",
    ]
    assert all(profile["simulated"] for profile in controller.profiles)
    assert [profile["initials"] for profile in controller.profiles] == [
        "A1", "T1", "S2",
    ]


def test_controller_selection_and_navigation_fail_safely() -> None:
    controller = WalletController()

    assert controller.selectProfile("trading")
    assert controller.activeProfileId == "trading"
    assert not controller.selectProfile("unknown")
    assert controller.activeProfileId == "trading"

    controller.showWallets()
    assert controller.currentScreen == "wallets"
    controller.showMain()
    assert controller.currentScreen == "main"
