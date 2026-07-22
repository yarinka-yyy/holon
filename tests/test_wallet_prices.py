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
    portfolio_to_map,
)
from holon_wallet.public_data import PublicDataStatus

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
    assert model["assets"][1]["amount"] == "5 USDC"
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
    assert estimate_asset_usd(1_000_000, 6, "usdc", snapshot) == "≈ $1.00"
    unavailable = replace(snapshot, prices=(replace(snapshot.prices[0], answer=None),))
    assert estimate_wei_usd(1, unavailable) == "Data unavailable"
