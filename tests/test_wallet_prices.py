from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from decimal import Decimal

from requests import exceptions as request_errors

from holon_contracts.registry import load_registry

from holon_wallet.prices import (
    PRICE_FEEDS,
    SEQUENCER_FEED,
    AssetPrice,
    COINGECKO_SIMPLE_PRICE_URL,
    MarketPrice,
    MarketPriceSnapshot,
    MarketPriceStatus,
    PortfolioMarketPriceService,
    PriceService,
    PriceSnapshot,
    PriceStatus,
    estimate_wei_usd,
    estimate_asset_usd,
    format_usd,
    is_unusually_high_base_fee,
    portfolio_to_map,
)
from holon_wallet.lending_view import _updated_text, lending_portfolio_to_map
from holon_wallet.public_data import AssetReadError, PublicDataStatus
from holon_lending import LendingPortfolioService

from wallet_public_support import public_snapshot


NOW = 2_000_000_000


class FakeMarketResponse:
    def __init__(self, status_code: int, body: object) -> None:
        self.status_code = status_code
        self.body = body

    def json(self) -> object:
        return self.body


def market_body() -> dict[str, dict[str, object]]:
    return {
        item.coingecko_id: {"usd": "1.25", "last_updated_at": NOW - 10}
        for item in load_registry().market_prices
    }


def market_snapshot(**values: str) -> MarketPriceSnapshot:
    return MarketPriceSnapshot(
        tuple(
            MarketPrice(
                item.market_price_id,
                item.coingecko_id,
                MarketPriceStatus.LIVE,
                Decimal(values.get(item.market_price_id, "1")),
                NOW - 10,
            )
            for item in load_registry().market_prices
        ),
        NOW,
    )


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


def test_market_prices_use_one_privacy_safe_request_for_every_pinned_asset() -> None:
    calls: list[tuple[str, dict[str, str], float]] = []

    def get(url: str, *, params: dict[str, str], timeout: float):
        calls.append((url, params, timeout))
        return FakeMarketResponse(200, market_body())

    snapshot = PortfolioMarketPriceService(get, lambda: NOW).refresh()

    assert len(calls) == 1
    assert calls[0][0] == COINGECKO_SIMPLE_PRICE_URL
    assert calls[0][2] == 5.0
    assert set(calls[0][1]) == {
        "ids", "vs_currencies", "include_last_updated_at",
    }
    request_text = repr(calls[0]).lower()
    assert all(word not in request_text for word in (
        "address", "profile", "balance", "rpc", "wallet",
    ))
    assert snapshot.has_live
    assert all(item.status is MarketPriceStatus.LIVE for item in snapshot.prices)


def test_market_prices_keep_valid_partial_response_and_reject_stale_or_malformed_items() -> None:
    body = market_body()
    body.pop("optimism")
    body["arbitrum"]["last_updated_at"] = NOW - 301
    body["dai"]["usd"] = "not-a-price"

    snapshot = PortfolioMarketPriceService(
        lambda *_args, **_kwargs: FakeMarketResponse(200, body),
        lambda: NOW,
    ).refresh()

    assert snapshot.by_market["eth-usd"].status is MarketPriceStatus.LIVE
    assert snapshot.by_market["op-usd"].error_code == "PRICE_UNAVAILABLE"
    assert snapshot.by_market["arb-usd"].error_code == "PRICE_INVALID"
    assert snapshot.by_market["dai-usd"].error_code == "PRICE_INVALID"


def test_market_prices_retry_once_for_timeout_and_rate_limit() -> None:
    for first in (request_errors.Timeout(), FakeMarketResponse(429, {})):
        responses = iter((first, FakeMarketResponse(200, market_body())))
        calls = 0

        def get(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            value = next(responses)
            if isinstance(value, BaseException):
                raise value
            return value

        snapshot = PortfolioMarketPriceService(get, lambda: NOW).refresh()
        assert calls == 2
        assert snapshot.has_live


def test_network_sort_keeps_gas_first_but_all_networks_uses_dollar_value() -> None:
    snapshot = public_snapshot("polygon", usdc=1_000_000_000)
    assets = tuple(
        replace(item, atomic_units={
            "pol": 10**18,
            "wbtc": 2_000_000,
        }.get(item.asset_id, item.atomic_units))
        for item in snapshot.assets
    )
    snapshot = replace(snapshot, assets=assets)
    prices = market_snapshot(
        **{
            "usdc-usd": "1",
            "pol-usd": "0.5",
            "wbtc-polygon-usd": "25000",
        }
    )

    polygon = portfolio_to_map({"polygon": snapshot}, prices, "polygon")
    assert [item["assetId"] for item in polygon["assets"][:3]] == [
        "pol", "usdc", "wbtc",
    ]
    combined = portfolio_to_map({"polygon": snapshot}, prices, "all")
    assert [item["assetId"] for item in combined["assets"][:3]] == [
        "usdc", "wbtc", "pol",
    ]


def test_zero_filter_keeps_only_selected_network_gas_and_all_can_be_empty() -> None:
    snapshots = {
        network_id: public_snapshot(network_id, eth=0, usdc=0)
        for network_id in ("ethereum", "base", "arbitrum", "optimism", "polygon", "bsc")
    }
    prices = market_snapshot()

    polygon = portfolio_to_map(
        snapshots, prices, "polygon", show_zero_balances=False,
    )
    assert [item["assetId"] for item in polygon["assets"]] == ["pol"]
    assert polygon["totalUsd"] == "$0.00"

    combined = portfolio_to_map(
        snapshots, prices, "all", show_zero_balances=False,
    )
    assert combined["assets"] == []
    assert combined["totalUsd"] == "$0.00"

    full = portfolio_to_map(
        snapshots, prices, "all", show_zero_balances=True,
    )
    assert {item["assetId"] for item in full["assets"]}.issuperset({
        "eth", "pol", "bnb", "usdc", "op", "arb",
    })


def test_zero_filter_hides_fresh_subcent_dust_but_full_list_restores_it() -> None:
    optimism = public_snapshot("optimism", eth=0, usdc=0)
    optimism = replace(optimism, assets=tuple(
        replace(item, atomic_units=10**12) if item.asset_id == "op" else item
        for item in optimism.assets
    ))
    filtered = portfolio_to_map(
        {"optimism": optimism}, market_snapshot(), "optimism",
        show_zero_balances=False,
    )
    assert [item["assetId"] for item in filtered["assets"]] == ["eth"]
    assert filtered["totalUsd"] == "$0.00"

    full = portfolio_to_map(
        {"optimism": optimism}, market_snapshot(), "optimism",
        show_zero_balances=True,
    )
    op = next(item for item in full["assets"] if item["assetId"] == "op")
    assert op["amount"] != "0 OP"
    assert op["usd"] == "$0.00"
    assert full["totalUsd"] == filtered["totalUsd"]

    all_networks = portfolio_to_map(
        {"optimism": optimism}, market_snapshot(), "all",
        show_zero_balances=False,
    )
    assert all_networks["assets"] == []


def test_zero_filter_never_hides_unknown_partial_or_unavailable_assets() -> None:
    optimism = public_snapshot("optimism", eth=0, usdc=0)
    optimism = replace(optimism, assets=tuple(
        replace(item, atomic_units=10**12) if item.asset_id == "op" else item
        for item in optimism.assets
    ))
    prices = market_snapshot()
    prices = replace(prices, prices=tuple(
        replace(item, status=MarketPriceStatus.UNAVAILABLE, value_usd=None)
        if item.market_price_id == "op-usd" else item
        for item in prices.prices
    ))
    unknown = portfolio_to_map(
        {"optimism": optimism}, prices, "optimism",
        show_zero_balances=False,
    )
    assert any(item["assetId"] == "op" for item in unknown["assets"])

    polygon = public_snapshot("polygon", eth=0, usdc=0)
    polygon = replace(
        polygon,
        status=PublicDataStatus.PARTIAL,
        asset_errors=(AssetReadError("dai", "RPC_UNAVAILABLE"),),
    )
    partial = portfolio_to_map(
        {"polygon": polygon}, market_snapshot(), "polygon",
        show_zero_balances=False,
    )
    assert len(partial["assets"]) == len(polygon.assets)
    assert next(item for item in partial["assets"] if item["assetId"] == "dai")["saved"]
    assert not next(item for item in partial["assets"] if item["assetId"] == "usdc")["saved"]

    unavailable = portfolio_to_map(
        {"base": public_snapshot("base", PublicDataStatus.UNAVAILABLE)},
        market_snapshot(), "base", show_zero_balances=False,
    )
    assert unavailable["assets"]
    assert all(item["amount"] == "Data unavailable" for item in unavailable["assets"])


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
    assert unavailable["assets"][0]["amount"] == "1 ETH"
    assert unavailable["assets"][0]["incomplete"] is True

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


def test_six_network_aggregation_keeps_zero_unpriced_assets_but_fails_closed_for_nonzero() -> None:
    prices = PriceSnapshot(
        8453, PriceStatus.LIVE,
        (
            AssetPrice("eth", "ETH", PriceStatus.LIVE, 250_000_000_000, 8, NOW),
            AssetPrice("usdc", "USDC", PriceStatus.LIVE, 100_000_000, 8, NOW),
        ), NOW,
    )
    snapshots = {
        network_id: public_snapshot(network_id)
        for network_id in ("ethereum", "base", "arbitrum", "optimism", "polygon", "bsc")
    }
    zero = portfolio_to_map(snapshots, prices, "all")
    assert zero["totalAvailable"] is True
    assert next(item for item in zero["assets"] if item["assetId"] == "eth")["amount"] == "4 ETH"
    assert next(item for item in zero["assets"] if item["assetId"] == "op")["usd"] == "$0.00"

    optimism = snapshots["optimism"]
    assets = tuple(
        replace(item, atomic_units=10**18) if item.asset_id == "op" else item
        for item in optimism.assets
    )
    snapshots["optimism"] = replace(optimism, assets=assets)
    nonzero = portfolio_to_map(snapshots, prices, "all")
    assert nonzero["totalAvailable"] is False
    assert nonzero["totalUsd"] == "$ —"
    assert next(item for item in nonzero["assets"] if item["assetId"] == "op")["amount"] == "1 OP"


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
    assert [item["assetId"] for item in combined["assets"][:6]] == [
        "eth", "usdc", "usdt", "weth", "dai", "cbbtc",
    ]
    assert {item["assetId"] for item in combined["assets"][6:]} == {
        "aave-v3", "compound-v3",
    }
    assert combined["networks"][1]["totalUsd"] == "$5,033.00"
    assert next(
        item for item in combined["assets"] if item["assetId"] == "compound-v3"
    )["amount"] == "20.00 USDC"

    ethereum = portfolio_to_map(snapshots, prices, "ethereum", lending)
    assert ethereum["totalUsd"] == "$2,502.00"
    assert [item["assetId"] for item in ethereum["assets"]] == [
        "eth", "usdc", "usdt", "weth", "dai", "cbbtc",
    ]

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


def test_lending_update_time_uses_the_computer_local_timezone() -> None:
    fetched_at = "2026-07-30T14:00:00Z"
    expected = datetime.fromisoformat(fetched_at.replace("Z", "+00:00"))
    expected_text = "Cached · " + expected.astimezone().strftime("%H:%M")

    assert _updated_text({"source": "MEMORY_CACHE", "fetched_at": fetched_at}) == expected_text


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
