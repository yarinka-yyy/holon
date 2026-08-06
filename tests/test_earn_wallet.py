from __future__ import annotations

from dataclasses import replace

from holon_earn import (
    AvailabilityState,
    EarnPortfolioService,
    EarnPortfolioSnapshot,
    EarnProviderResult,
    ExitConstraints,
    FreshnessState,
    MetricKind,
    PortfolioState,
    ProductCategory,
    ProviderSource,
    ProviderState,
    RiskAssessment,
    YieldMetric,
    YieldPosition,
    YieldProduct,
)
from holon_wallet.earn_view import earn_portfolio_to_map
from holon_wallet.prices import AssetPrice, PriceSnapshot, PriceStatus, portfolio_to_map
from wallet_public_support import public_snapshot

ACCOUNT = {
    "label": "Main Account",
    "address": "0x1111111111111111111111111111111111111111",
}
NOW = 1_786_000_000
OBSERVED = "2026-08-06T12:00:00Z"


def _yield_product(
    provider: str,
    category: ProductCategory,
    network: str,
    amount: str,
    *,
    cached: bool = False,
) -> YieldProduct:
    trailing = category is ProductCategory.VAULT
    return YieldProduct(
        f"{provider}:product", provider, category,
        "aave-v3" if not trailing else "hlp", "Aave V3" if not trailing else "HLP Fixture",
        network, ("usdc",),
        YieldPosition(
            "usdc", amount, amount if trailing else None, AvailabilityState.AVAILABLE,
        ),
        (YieldMetric(
            MetricKind.TRAILING_RETURN if trailing else MetricKind.SUPPLY_APY,
            "8.5" if trailing else "4.2", "30d" if trailing else None,
            AvailabilityState.AVAILABLE,
        ),),
        FreshnessState.CACHED if cached else FreshnessState.LIVE,
        AvailabilityState.AVAILABLE, OBSERVED,
        ExitConstraints(
            AvailabilityState.AVAILABLE, None,
            limitations=("Exit depends on available liquidity.",),
        ),
        RiskAssessment(),
    )


def _provider(
    provider: str,
    category: ProductCategory,
    network: str,
    amount: str | None,
    *,
    cached: bool = False,
) -> EarnProviderResult:
    products = (
        (_yield_product(provider, category, network, amount, cached=cached),)
        if amount is not None else ()
    )
    return EarnProviderResult(
        provider, category, (network,),
        ProviderState.DEGRADED if cached or amount is None else ProviderState.READY,
        ProviderSource.CACHED if cached else ProviderSource.UNAVAILABLE if amount is None else ProviderSource.LIVE,
        products, OBSERVED if products else None,
        "EARN_PROVIDER_CACHED" if cached else "EARN_PROVIDER_UNAVAILABLE" if amount is None else "EARN_PROVIDER_READY",
        "Provider data is available." if products else "Provider data is unavailable.",
    )


def _snapshot(vault_amount: str | None, *, cached: bool = False) -> EarnPortfolioSnapshot:
    providers = (
        _provider("holon.lending", ProductCategory.LENDING, "base", "10"),
        _provider("fixture.vault", ProductCategory.VAULT, "hyperliquid", vault_amount, cached=cached),
    )
    complete = vault_amount is not None
    return EarnPortfolioSnapshot(
        PortfolioState.READY if complete and not cached else PortfolioState.PARTIAL,
        ACCOUNT, providers, complete,
        "EARN_PORTFOLIO_READY" if complete and not cached else "EARN_PORTFOLIO_PARTIAL",
        "Earn portfolio is available." if complete else "Some Earn data is unavailable.",
    )


def _prices() -> PriceSnapshot:
    return PriceSnapshot(
        8453, PriceStatus.LIVE,
        (
            AssetPrice("eth", "ETH", PriceStatus.LIVE, 250_000_000_000, 8, NOW),
            AssetPrice("usdc", "USDC", PriceStatus.LIVE, 100_000_000, 8, NOW),
        ), NOW,
    )


def test_all_networks_counts_each_earn_product_once_and_network_filter_is_independent() -> None:
    snapshots = {
        "ethereum": public_snapshot("ethereum", eth=10**18, usdc=2_500_000),
        "base": public_snapshot("base", eth=10**18, usdc=2_500_000),
    }
    earn = _snapshot("20")

    all_networks = portfolio_to_map(snapshots, _prices(), "all", earn)
    base = portfolio_to_map(snapshots, _prices(), "base", earn)
    ethereum = portfolio_to_map(snapshots, _prices(), "ethereum", earn)

    assert all_networks["totalUsd"] == "$5,035.00"
    assert all_networks["earnComplete"] is True
    assert base["totalUsd"] == "$2,512.50"
    assert ethereum["totalUsd"] == "$2,502.50"
    assert ethereum["lendingComplete"] is True
    assert {item["assetId"] for item in all_networks["assets"][-2:]} == {
        "aave-v3", "fixture.vault:product",
    }


def test_uncached_installed_provider_marks_all_incomplete_but_not_unrelated_network() -> None:
    snapshots = {
        "ethereum": public_snapshot("ethereum"),
        "base": public_snapshot("base"),
    }
    earn = _snapshot(None)

    all_networks = portfolio_to_map(snapshots, _prices(), "all", earn)
    base = portfolio_to_map(snapshots, _prices(), "base", earn)

    assert all_networks["totalAvailable"] is False
    assert all_networks["earnComplete"] is False
    assert base["totalAvailable"] is True
    assert base["earnComplete"] is True


def test_cached_vault_remains_visible_with_typed_metric_and_no_risk_score() -> None:
    earn = _snapshot("20", cached=True)
    view = earn_portfolio_to_map(earn, _prices(), "vaults")

    assert [item["id"] for item in view["availableFilters"]] == [
        "all", "lending", "vaults",
    ]
    assert view["showLending"] is False and view["showVaults"] is True
    vault = view["vaultProducts"][0]
    assert vault["metricLabel"] == "Trailing return · 30d"
    assert vault["metricValue"] == "8.50%"
    assert vault["dataState"] == "CACHED"
    assert vault["riskState"] == "Not assessed" and vault["riskBand"] == ""


def test_vault_exit_conditions_remain_separate_from_risk() -> None:
    product = replace(
        _yield_product("fixture.vault", ProductCategory.VAULT, "hyperliquid", "20"),
        exit_constraints=ExitConstraints(
            AvailabilityState.AVAILABLE, False,
            notice_period="7d", lockup_period="30d", fees=("1% exit fee",),
            limitations=("Exit depends on available liquidity.",),
        ),
    )
    provider = EarnProviderResult(
        "fixture.vault", ProductCategory.VAULT, ("hyperliquid",),
        ProviderState.READY, ProviderSource.LIVE, (product,), OBSERVED,
        "EARN_PROVIDER_READY", "Provider data is available.",
    )
    snapshot = EarnPortfolioSnapshot(
        PortfolioState.READY, ACCOUNT, (provider,), True,
        "EARN_PORTFOLIO_READY", "Earn portfolio is available.",
    )

    vault = earn_portfolio_to_map(snapshot, _prices(), "vaults")["vaultProducts"][0]

    assert vault["exitConstraints"] == (
        "No immediate exit · Notice 7d · Lockup 30d · 1% exit fee · "
        "Exit depends on available liquidity."
    )
    assert vault["riskState"] == "Not assessed"


def test_base_earn_view_has_no_vault_filter() -> None:
    lending = _provider("holon.lending", ProductCategory.LENDING, "base", "0")
    snapshot = EarnPortfolioSnapshot(
        PortfolioState.READY, ACCOUNT, (lending,), True,
        "EARN_PORTFOLIO_READY", "Earn portfolio is available.",
    )
    view = earn_portfolio_to_map(snapshot, _prices(), "all")
    assert [item["id"] for item in view["availableFilters"]] == ["all", "lending"]
    assert view["showVaults"] is False


def test_unavailable_earn_snapshot_never_marks_all_networks_complete() -> None:
    snapshots = {
        "ethereum": public_snapshot("ethereum"),
        "base": public_snapshot("base"),
    }
    unavailable = EarnPortfolioService.unavailable(ACCOUNT)

    result = portfolio_to_map(snapshots, _prices(), "all", unavailable)

    assert result["earnComplete"] is False
    assert result["lendingComplete"] is False
    assert result["totalAvailable"] is False
