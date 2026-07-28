from __future__ import annotations

import pytest

from holon_wallet.controller import WalletController
from holon_wallet.prices import AssetPrice, PriceSnapshot, PriceStatus
from holon_wallet.public_cache import PublicCacheStore
from holon_wallet.public_data import PublicDataStatus
from holon_wallet.storage import StorageError, WalletPaths, atomic_write_json
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic
from wallet_public_support import (
    DeferredExecutor, ImmediateExecutor, StubLendingPortfolioService,
    StubPriceService, StubPublicDataService, public_snapshot,
)


def prices() -> PriceSnapshot:
    return PriceSnapshot(
        8453,
        PriceStatus.LIVE,
        (
            AssetPrice("eth", "ETH", PriceStatus.LIVE, 250_000_000_000, 8, 10),
            AssetPrice("usdc", "USDC", PriceStatus.LIVE, 100_000_000, 8, 10),
        ),
        10,
    )


def test_public_cache_round_trip_is_profile_and_address_bound(tmp_path) -> None:
    store = PublicCacheStore(WalletPaths(tmp_path))
    first_address = "0x1111111111111111111111111111111111111111"
    second_address = "0x2222222222222222222222222222222222222222"
    store.save(
        "profile-1", first_address,
        {"ethereum": public_snapshot("ethereum"), "base": public_snapshot("base")},
        prices(),
    )
    store.save(
        "profile-2", second_address,
        {"base": public_snapshot("base", usdc=9_000_000)}, prices(),
    )

    first = store.load("profile-1", first_address)
    second = store.load("profile-2", second_address)
    assert first is not None and {item.network_id for item in first.networks} == {
        "ethereum", "base",
    }
    assert second is not None and second.networks[0].usdc.atomic_units == 9_000_000
    assert store.load("profile-1", second_address) is None
    assert "secret" not in store.path.read_text(encoding="utf-8").lower()


def test_corrupt_cache_is_ignored_and_atomic_failure_is_reported(
    tmp_path, monkeypatch,
) -> None:
    store = PublicCacheStore(WalletPaths(tmp_path))
    atomic_write_json(store.path, {"schema_version": 999, "profiles": []})
    assert store.load("profile-1", "0x" + "11" * 20) is None

    def fail(*_args, **_kwargs):
        raise StorageError("fixture")

    monkeypatch.setattr("holon_wallet.public_cache.atomic_write_json", fail)
    with pytest.raises(StorageError):
        store.save(
            "profile-1", "0x" + "11" * 20,
            {"base": public_snapshot("base")}, prices(),
        )


def test_controller_displays_cache_immediately_while_refresh_is_pending(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    profile = repository.new_record(generate_mnemonic(), "Main Account")
    repository.create_new("fixture-password", profile)
    PublicCacheStore(repository.paths).save(
        profile.summary.profile_id, profile.summary.address,
        {
            "ethereum": public_snapshot("ethereum", eth=2 * 10**18),
            "base": public_snapshot("base", usdc=7_000_000),
        },
        prices(),
    )
    deferred = DeferredExecutor()
    controller = WalletController(
        repository,
        StubPublicDataService(),
        public_data_executor=deferred,
        price_service=StubPriceService(),
        lending_portfolio_service=StubLendingPortfolioService(),
    )
    try:
        assert controller.currentScreen == "main"
        assert controller.publicDataRefreshing
        assert controller.publicDataUpdatedText == "Cached · updating…"
        assert "CACHED DATA" in controller.publicDataBanner
        assert controller.portfolioData["totalAvailable"] is True
        assert controller.portfolioData["assets"][1]["amount"] == "9.50 USDC"
        assert controller.maximumTransferAmount(
            "base", "usdc", "0x" + "44" * 20,
        ) == ""
    finally:
        controller.shutdown()


def test_failed_refresh_preserves_last_known_values_and_marks_them_cached(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    profile = repository.new_record(generate_mnemonic(), "Main Account")
    repository.create_new("fixture-password", profile)
    PublicCacheStore(repository.paths).save(
        profile.summary.profile_id, profile.summary.address,
        {"ethereum": public_snapshot("ethereum"), "base": public_snapshot("base")},
        prices(),
    )
    service = StubPublicDataService({
        "ethereum": PublicDataStatus.UNAVAILABLE,
        "base": PublicDataStatus.UNAVAILABLE,
    })
    controller = WalletController(
        repository,
        service,
        public_data_executor=ImmediateExecutor(),
        price_service=StubPriceService(PriceStatus.UNAVAILABLE),
        lending_portfolio_service=StubLendingPortfolioService(),
    )
    try:
        assert not controller.publicDataRefreshing
        assert "CACHED PUBLIC DATA" in controller.publicDataBanner
        assert controller.portfolioData["totalAvailable"] is True
        assert controller.baseData["usdcValue"] == "2.5 USDC"
    finally:
        controller.shutdown()


def test_partial_refresh_atomically_keeps_old_network_and_saves_live_one(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    profile = repository.new_record(generate_mnemonic(), "Main Account")
    repository.create_new("fixture-password", profile)
    store = PublicCacheStore(repository.paths)
    store.save(
        profile.summary.profile_id, profile.summary.address,
        {
            "ethereum": public_snapshot("ethereum", eth=3 * 10**18),
            "base": public_snapshot("base", usdc=7_000_000),
        },
        prices(),
    )
    controller = WalletController(
        repository,
        StubPublicDataService({
            "ethereum": PublicDataStatus.UNAVAILABLE,
            "base": PublicDataStatus.LIVE,
        }),
        public_data_executor=ImmediateExecutor(),
        price_service=StubPriceService(PriceStatus.UNAVAILABLE),
        lending_portfolio_service=StubLendingPortfolioService(),
    )
    try:
        assert controller.ethereumData["ethValue"] == "3 ETH"
        assert controller.baseData["usdcValue"] == "2.5 USDC"
        assert "CACHED PUBLIC DATA" in controller.publicDataBanner
    finally:
        controller.shutdown()

    saved = store.load(profile.summary.profile_id, profile.summary.address)
    assert saved is not None
    by_network = {item.network_id: item for item in saved.networks}
    assert by_network["ethereum"].eth.atomic_units == 3 * 10**18
    assert by_network["base"].usdc.atomic_units == 2_500_000
