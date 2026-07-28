from __future__ import annotations

from dataclasses import replace
from decimal import Decimal

from holon_wallet.prices import (
    PRICE_FEEDS,
    SEQUENCER_FEED,
    AssetPrice,
    PriceService,
    PriceSnapshot,
    PriceStatus,
    estimate_wei_usd,
    estimate_asset_usd,
    format_usd,
    is_unusually_high_base_fee,
    portfolio_to_map,
)
from holon_wallet.lending_view import lending_portfolio_to_map
from holon_wallet.public_data import PublicDataStatus
from holon_lending import LendingPortfolioService

from wallet_public_support import public_snapshot


NOW = 2_000_000_000


class FakeChainlinkRpc:
    def __init__(self, chain_id: int = 8453) -> None:
        self.observed_chain_id = chain_id
        self.values = {
            SEQUENCER_FEED: (10, 0, NOW - 7_200, NOW - 5, 10),
            PRICE_FEEDS[0].contract: (20, 250_012_345_678, NOW - 100, NOW - 100, 20),
            PRICE_FEEDS[1].contract: (30, 99_990_000, NOW - 100, NOW - 100, 30),
        }
        self.feed_decimals = {
            SEQUENCER_FEED: 0,
            PRICE_FEEDS[0].contract: 8,
            PRICE_FEEDS[1].contract: 8,
        }

    def chain_id(self) -> int:
        return self.observed_chain_id

    def decimals(self, contract: str) -> int:
        return self.feed_decimals[contract]

    def latest_round_data(self, contract: str):
        return self.values[contract]


def test_chainlink_snapshot_validates_sequencer_and_prices() -> None:
    rpc = FakeChainlinkRpc()
    snapshot = PriceService(lambda _endpoint: rpc, {}, lambda: NOW).refresh()

    assert snapshot.status is PriceStatus.LIVE
    assert snapshot.by_asset["eth"].value == Decimal("2500.12345678")
    assert snapshot.by_asset["usdc"].value == Decimal("0.9999")


def test_chainlink_fails_closed_for_chain_sequencer_and_grace_period() -> None:
    wrong_chain = PriceService(
        lambda _endpoint: FakeChainlinkRpc(1), {}, lambda: NOW,
    ).refresh()
    assert wrong_chain.status is PriceStatus.UNAVAILABLE
    assert wrong_chain.error_code == "WRONG_CHAIN"

    down_rpc = FakeChainlinkRpc()
    down_rpc.values[SEQUENCER_FEED] = (10, 1, NOW - 7_200, NOW - 5, 10)
    down = PriceService(lambda _endpoint: down_rpc, {}, lambda: NOW).refresh()
    assert down.error_code == "SEQUENCER_DOWN"

    grace_rpc = FakeChainlinkRpc()
    grace_rpc.values[SEQUENCER_FEED] = (10, 0, NOW - 3_600, NOW - 5, 10)
    grace = PriceService(lambda _endpoint: grace_rpc, {}, lambda: NOW).refresh()
    assert grace.error_code == "SEQUENCER_GRACE_PERIOD"


def test_invalid_round_and_stale_feed_hide_only_usd_values() -> None:
    rpc = FakeChainlinkRpc()
    eth_spec = PRICE_FEEDS[0]
    rpc.values[eth_spec.contract] = (
        20, 250_000_000_000, NOW - 2_000, NOW - eth_spec.max_age_seconds - 1, 20,
    )
    snapshot = PriceService(lambda _endpoint: rpc, {}, lambda: NOW).refresh()

    assert snapshot.status is PriceStatus.UNAVAILABLE
    assert snapshot.by_asset["eth"].status is PriceStatus.UNAVAILABLE
    assert snapshot.by_asset["usdc"].status is PriceStatus.LIVE


def test_retry_happens_once_without_exposing_endpoint() -> None:
    calls = 0

    def factory(endpoint: str):
        nonlocal calls
        calls += 1
        assert endpoint == "https://private-token.example"
        raise TimeoutError()

    snapshot = PriceService(
        factory,
        {"HOLON_BASE_RPC_URL": "https://private-token.example"},
        lambda: NOW,
    ).refresh()
    assert calls == 2
    assert snapshot.error_code == "RPC_UNAVAILABLE"
    assert "private-token" not in repr(snapshot)


def test_portfolio_totals_and_breakdown_are_exact_and_fail_closed() -> None:
    prices = PriceSnapshot(
        8453,
        PriceStatus.LIVE,
        (
            AssetPrice("eth", "ETH", PriceStatus.LIVE, 250_000_000_000, 8, NOW),
            AssetPrice("usdc", "USDC", PriceStatus.LIVE, 100_000_000, 8, NOW),
        ),
        NOW,
    )
    snapshots = {
        "ethereum": public_snapshot("ethereum", eth=10**18, usdc=2_000_000),
        "base": public_snapshot("base", eth=2 * 10**18, usdc=3_000_000),
    }

    model = portfolio_to_map(snapshots, prices, "all")
    assert model["totalAvailable"] is True
    assert model["totalUsd"] == "$7,505.00"
    assert model["assets"][0]["amount"] == "3 ETH"
    assert model["assets"][1]["amount"] == "5.00 USDC"
    assert len(model["assets"][0]["breakdown"]) == 2

    snapshots["base"] = public_snapshot("base", PublicDataStatus.UNAVAILABLE)
    unavailable = portfolio_to_map(snapshots, prices, "all")
    assert unavailable["totalAvailable"] is False
    assert unavailable["totalUsd"] == "$ —"
    assert unavailable["assets"][0]["amount"] == "Data unavailable"

    ethereum = portfolio_to_map(snapshots, prices, "ethereum")
    assert ethereum["totalAvailable"] is True
    assert ethereum["totalUsd"] == "$2,502.00"


def test_decimal_format_and_fee_estimate_do_not_use_float() -> None:
    snapshot = PriceSnapshot(
        8453,
        PriceStatus.LIVE,
        (
            AssetPrice("eth", "ETH", PriceStatus.LIVE, 250_000_000_000, 8, NOW),
            AssetPrice("usdc", "USDC", PriceStatus.LIVE, 100_000_000, 8, NOW),
        ),
        NOW,
    )
    assert format_usd(Decimal("1.005")) == "$1.01"
    assert estimate_wei_usd(100_000_000_000_000, snapshot) == "≈ $0.25"
    assert not is_unusually_high_base_fee(19_999_999_999_999, snapshot)
    assert is_unusually_high_base_fee(20_000_000_000_000, snapshot)
    assert estimate_asset_usd(1_000_000, 6, "usdc", snapshot) == "≈ $1.00"
    unavailable = replace(snapshot, prices=(replace(snapshot.prices[0], answer=None),))
    assert estimate_wei_usd(1, unavailable) == "Data unavailable"
    assert is_unusually_high_base_fee(20_000_000_000_000, unavailable)


def test_lending_positions_extend_all_and_base_without_double_counting() -> None:
    prices = PriceSnapshot(
        8453, PriceStatus.LIVE,
        (
            AssetPrice("eth", "ETH", PriceStatus.LIVE, 250_000_000_000, 8, NOW),
            AssetPrice("usdc", "USDC", PriceStatus.LIVE, 100_000_000, 8, NOW),
        ), NOW,
    )
    snapshots = {
        "ethereum": public_snapshot("ethereum", eth=10**18, usdc=2_000_000),
        "base": public_snapshot("base", eth=2 * 10**18, usdc=3_000_000),
    }
    lending = [
        {"protocol": "aave-v3", "position_atomic": "10000000", "data_state": "LIVE"},
        {"protocol": "compound-v3", "position_atomic": "20000000", "data_state": "LIVE"},
        {"protocol": "morpho-v1", "position_atomic": "0", "data_state": "LIVE"},
    ]

    combined = portfolio_to_map(snapshots, prices, "all", lending)
    assert combined["totalUsd"] == "$7,535.00"
    assert [item["assetId"] for item in combined["assets"]] == [
        "eth", "usdc", "aave-v3", "compound-v3",
    ]
    assert combined["networks"][1]["totalUsd"] == "$5,033.00"
    assert combined["assets"][2]["amount"] == "10.00 USDC"

    ethereum = portfolio_to_map(snapshots, prices, "ethereum", lending)
    assert ethereum["totalUsd"] == "$2,502.00"
    assert [item["assetId"] for item in ethereum["assets"]] == ["eth", "usdc"]

    lending[1]["position_atomic"] = None
    incomplete = portfolio_to_map(snapshots, prices, "base", lending)
    assert incomplete["totalAvailable"] is False
    assert incomplete["totalUsd"] == "$ —"


def test_lending_cards_hide_only_live_confirmed_zero_positions() -> None:
    prices = PriceSnapshot(
        8453, PriceStatus.LIVE,
        (AssetPrice("usdc", "USDC", PriceStatus.LIVE, 100_000_000, 8, NOW),),
        NOW,
    )
    payload = LendingPortfolioService.unavailable({
        "label": "Main Account",
        "address": "0x1111111111111111111111111111111111111111",
    })
    payload["protocols"][0].update({
        "position_atomic": "1000000", "display_position": "1 USDC",
        "data_state": "CACHED",
    })
    payload["protocols"][1].update({
        "position_atomic": "0", "display_position": "0 USDC", "data_state": "LIVE",
    })
    result = lending_portfolio_to_map(payload, prices)
    assert [item["protocol"] for item in result["visibleProtocols"]] == [
        "aave-v3", "morpho-v1",
    ]
    assert [item["protocol"] for item in result["emptyProtocols"]] == ["compound-v3"]
    assert result["hiddenProtocolCount"] == 1

    payload["protocols"][1]["data_state"] = "CACHED"
    cached = lending_portfolio_to_map(payload, prices)
    assert cached["hiddenProtocolCount"] == 0
    assert [item["protocol"] for item in cached["visibleProtocols"]] == [
        "aave-v3", "compound-v3", "morpho-v1",
    ]


def test_wallet_lending_display_uses_two_decimal_usdc_and_rates() -> None:
    prices = PriceSnapshot(
        8453, PriceStatus.LIVE,
        (AssetPrice("usdc", "USDC", PriceStatus.LIVE, 100_000_000, 8, NOW),),
        NOW,
    )
    payload = LendingPortfolioService.unavailable({
        "label": "Main Account",
        "address": "0x1111111111111111111111111111111111111111",
    })
    payload["summary"].update({
        "total_position_atomic": "999999",
        "tracked_earnings_atomic": "-1",
        "earnings_status": "AVAILABLE",
        "weighted_confirmed_annual_percent": "3.460607",
    })
    payload["protocols"][0].update({
        "position_atomic": "999999",
        "tracked_earnings_atomic": "123456",
        "earnings_status": "AVAILABLE",
        "base_yield": {
            "value_percent": "3.460607",
            "comparison_apy_percent": "3.521234",
            "metric": "APR",
        },
        "incentives": {"total_apr_percent": "0.006789"},
        "confirmed_total_annual_percent": "3.467396",
    })

    result = lending_portfolio_to_map(payload, prices)

    assert result["totalPosition"] == "0.99 USDC"
    assert result["trackedEarnings"] == "0.00 USDC"
    assert result["weightedYield"] == "3.46%"
    aave = result["protocols"][0]
    assert aave["position"] == "0.99 USDC"
    assert aave["earnings"] == "0.12 USDC"
    assert aave["baseYield"] == "3.46% APR"
    assert aave["comparisonYield"] == "3.52%"
    assert aave["incentives"] == "0.01% APR"
    assert aave["confirmedTotal"] == "3.47%"
