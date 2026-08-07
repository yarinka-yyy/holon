from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import sys

from holon_earn import MetricKind, ProviderSource, ProviderState

ROOT = Path(__file__).parents[1]
PERPDEX_SRC = ROOT / "modules" / "perpdex" / "src"
sys.path.insert(0, str(PERPDEX_SRC))

from holon_perpdex.earn import HlpEarnProvider  # noqa: E402
from holon_perpdex.profile import HLP_ADDRESS, HLP_NAME  # noqa: E402
from holon_perpdex.reader import HyperliquidReader  # noqa: E402

ACCOUNT = {"address": "0x" + "12" * 20, "label": "Main"}


class FakeInfo:
    def __init__(self, *, referred_by=None, identity: str = HLP_ADDRESS) -> None:
        self.calls: list[dict[str, object]] = []
        self.referred_by = referred_by
        self.identity = identity

    def __call__(self, payload: Mapping[str, object]) -> object:
        self.calls.append(dict(payload))
        kind = payload["type"]
        if kind == "metaAndAssetCtxs":
            return [
                {"universe": [
                    {"maxLeverage": 40, "name": "BTC", "szDecimals": 5},
                    {"maxLeverage": 25, "name": "ETH", "szDecimals": 4},
                    {"maxLeverage": 20, "name": "SOL", "szDecimals": 2},
                ]},
                [
                    {"funding": "0.00001", "markPx": "60000", "openInterest": "10", "oraclePx": "60001"},
                    {"funding": "0.00002", "markPx": "3000", "openInterest": "20", "oraclePx": "3001"},
                    {"funding": "-0.00001", "markPx": "150", "openInterest": "30", "oraclePx": "151"},
                ],
            ]
        if kind == "l2Book":
            prices = {"BTC": ("59999", "60001"), "ETH": ("2999", "3001"), "SOL": ("149", "151")}
            bid, ask = prices[str(payload["coin"])]
            return {"coin": payload["coin"], "levels": [[{"px": bid}], [{"px": ask}]], "time": 1786000000000}
        if kind == "clearinghouseState":
            return {
                "assetPositions": [
                    {"position": {
                        "coin": "BTC", "entryPx": "59000", "leverage": {"type": "isolated", "value": 2},
                        "liquidationPx": "30000", "marginUsed": "25", "positionValue": "50",
                        "szi": "0.00084", "unrealizedPnl": "1.25",
                    }},
                    {"position": {
                        "coin": "DOGE", "entryPx": "0.2", "leverage": {"type": "cross", "value": 5},
                        "liquidationPx": None, "marginUsed": "1", "positionValue": "5",
                        "szi": "-25", "unrealizedPnl": "-0.1",
                    }},
                ],
                "marginSummary": {"accountValue": "125", "totalMarginUsed": "26", "totalNtlPos": "55"},
                "withdrawable": "90",
            }
        if kind == "frontendOpenOrders":
            return [{
                "coin": "BTC", "limitPx": "61000", "oid": 42, "orderType": "Limit",
                "reduceOnly": True, "side": "Sell", "sz": "0.00084", "timestamp": 1786000000000,
            }]
        if kind == "userFees":
            return {"userAddRate": "0.00015", "userCrossRate": "0.00045"}
        if kind == "referral":
            return {"cumVlm": "999999", "referredBy": self.referred_by}
        if kind == "vaultDetails":
            return {
                "allowDeposits": True,
                "apr": "0.12",
                "followerState": {
                    "allTimePnl": "3.5", "lockupUntil": 0, "pnl": "1.5", "vaultEquity": "20",
                },
                "isClosed": False,
                "name": HLP_NAME,
                "relationship": {"data": {"childAddresses": []}, "type": "parent"},
                "vaultAddress": self.identity,
            }
        if kind == "userVaultEquities":
            return [{"equity": "20", "vaultAddress": HLP_ADDRESS}]
        raise AssertionError(kind)


def _assert_no_float(value: object) -> None:
    assert not isinstance(value, float)
    if isinstance(value, Mapping):
        for item in value.values():
            _assert_no_float(item)
    elif isinstance(value, list):
        for item in value:
            _assert_no_float(item)


def test_reader_returns_supported_markets_and_all_positions_without_float() -> None:
    transport = FakeInfo()
    reader = HyperliquidReader(transport)
    markets = reader("markets", {})
    assert [item["market"] for item in markets["markets"]] == ["BTC", "ETH", "SOL"]
    assert markets["markets"][0]["spread_percent"] == "0.0033"
    portfolio = reader("portfolio", {"active_account": ACCOUNT})
    assert portfolio["status"] == "READY"
    assert [item["market"] for item in portfolio["positions"]] == ["BTC", "DOGE"]
    assert [item["supported"] for item in portfolio["positions"]] == [True, False]
    assert portfolio["orders"][0]["reduce_only"] is True
    _assert_no_float(markets)
    _assert_no_float(portfolio)


def test_referral_reads_only_referred_by_and_never_volume_history() -> None:
    absent_transport = FakeInfo(referred_by=None)
    absent = HyperliquidReader(absent_transport)("referral", {"active_account": ACCOUNT})
    assert absent["has_referrer"] is False
    assert absent["referred_by"] is None
    assert [call["type"] for call in absent_transport.calls] == ["referral"]

    existing_transport = FakeInfo(referred_by={"code": "SOMEONE", "referrer": "0x" + "34" * 20})
    existing = HyperliquidReader(existing_transport)("referral", {"active_account": ACCOUNT})
    assert existing["has_referrer"] is True
    assert existing["referred_by"] == "SOMEONE"


def test_hlp_identity_mismatch_fails_closed() -> None:
    result = HyperliquidReader(FakeInfo(identity="0x" + "00" * 20))(
        "hlp", {"active_account": ACCOUNT},
    )
    assert result["status"] == "UNAVAILABLE"
    assert result["code"] == "HLP_IDENTITY_MISMATCH"


def test_hlp_earn_provider_uses_protocol_apr_and_not_assessed_risk() -> None:
    provider = HlpEarnProvider(HyperliquidReader(FakeInfo()))
    result = provider.read(ACCOUNT, {})
    assert result.state is ProviderState.READY
    assert result.source is ProviderSource.LIVE
    product = result.products[0]
    assert product.position.amount == "20"
    assert product.position.value_usd == "20"
    assert product.metrics[0].kind is MetricKind.PROTOCOL_APR
    assert product.metrics[0].value_percent == "12"
    assert product.metrics[1].kind is MetricKind.TRAILING_RETURN
    assert product.metrics[1].value_percent is None
    assert product.risk.state == "NOT_ASSESSED"
