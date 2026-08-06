"""Bounded read-only Hyperliquid mainnet adapter with no WebSocket or credentials."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .profile import API_URL, HLP_ADDRESS, HLP_NAME, SUPPORTED_MARKETS

READ_SCHEMA_VERSION = "1"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HTTP_TIMEOUT_SECONDS = 10.0
_ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
_NUMBER_RE = re.compile(r"^-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")


class ReaderError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _observed_at() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _number(value: object, label: str, *, signed: bool = True) -> str:
    if type(value) is int:
        value = str(value)
    if not isinstance(value, str) or len(value) > 96 or _NUMBER_RE.fullmatch(value) is None:
        raise ReaderError("HYPERLIQUID_DATA_INVALID", f"Invalid {label}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ReaderError("HYPERLIQUID_DATA_INVALID", f"Invalid {label}") from exc
    if not parsed.is_finite() or not signed and parsed < 0:
        raise ReaderError("HYPERLIQUID_DATA_INVALID", f"Invalid {label}")
    return value


def _optional_number(value: object, label: str, *, signed: bool = True) -> str | None:
    return None if value is None else _number(value, label, signed=signed)


def _format_decimal(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _active_account(params: Mapping[str, object]) -> dict[str, str]:
    value = params.get("active_account")
    if (
        not isinstance(value, Mapping)
        or set(value) != {"address", "label"}
        or not isinstance(value.get("address"), str)
        or _ADDRESS_RE.fullmatch(value["address"]) is None
        or not isinstance(value.get("label"), str)
        or not value["label"]
        or len(value["label"]) > 64
    ):
        raise ReaderError("WALLET_ACCOUNT_UNAVAILABLE", "Active Wallet account is unavailable")
    return {"address": value["address"].lower(), "label": value["label"]}


class HttpInfoTransport:
    """One-attempt HTTPS JSON transport for the public `/info` endpoint."""

    def __init__(self, base_url: str = API_URL) -> None:
        if base_url != API_URL:
            raise ReaderError("HYPERLIQUID_ENDPOINT_INVALID", "Unsupported Hyperliquid endpoint")
        self.endpoint = base_url.rstrip("/") + "/info"

    def __call__(self, payload: Mapping[str, object]) -> object:
        body = json.dumps(
            dict(payload), ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        request = Request(
            self.endpoint,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "Holon/0.1"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise ReaderError("HYPERLIQUID_UNAVAILABLE", "Hyperliquid public data is unavailable") from exc
        if not raw or len(raw) > MAX_RESPONSE_BYTES:
            raise ReaderError("HYPERLIQUID_DATA_INVALID", "Invalid Hyperliquid response size")
        try:
            return json.loads(raw.decode("utf-8"), parse_float=str)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ReaderError("HYPERLIQUID_DATA_INVALID", "Invalid Hyperliquid response") from exc


class HyperliquidReader:
    OPERATIONS = frozenset({"fees", "hlp", "markets", "portfolio", "referral"})
    ACCOUNT_OPERATIONS = frozenset({"fees", "hlp", "portfolio", "referral"})

    def __init__(self, transport: Callable[[Mapping[str, object]], object] | None = None) -> None:
        self._post = transport or HttpInfoTransport()

    def __call__(self, operation: str, params: Mapping[str, object]) -> Mapping[str, object]:
        if operation not in self.OPERATIONS or not isinstance(params, Mapping):
            return self.unavailable(operation, "PERPDEX_READ_INVALID")
        try:
            if operation == "markets":
                return self.markets()
            account = _active_account(params)
            if operation == "portfolio":
                return self.portfolio(account)
            if operation == "fees":
                return self.fees(account)
            if operation == "referral":
                return self.referral(account)
            return self.hlp(account)
        except ReaderError as exc:
            return self.unavailable(operation, exc.code)
        except Exception:
            return self.unavailable(operation, "HYPERLIQUID_UNAVAILABLE")

    @staticmethod
    def unavailable(operation: str, code: str) -> dict[str, object]:
        return {
            "code": code,
            "message": "Hyperliquid public data is unavailable.",
            "observed_at": None,
            "operation": operation,
            "schema_version": READ_SCHEMA_VERSION,
            "status": "UNAVAILABLE",
        }

    def markets(self) -> dict[str, object]:
        response = self._post({"type": "metaAndAssetCtxs"})
        if not isinstance(response, list) or len(response) != 2:
            raise ReaderError("HYPERLIQUID_METADATA_INVALID", "Invalid market metadata")
        meta, contexts = response
        if (
            not isinstance(meta, Mapping)
            or not isinstance(meta.get("universe"), list)
            or not isinstance(contexts, list)
            or len(meta["universe"]) != len(contexts)
        ):
            raise ReaderError("HYPERLIQUID_METADATA_INVALID", "Invalid market metadata")
        by_name: dict[str, tuple[int, Mapping[str, object], Mapping[str, object]]] = {}
        for index, (asset, context) in enumerate(zip(meta["universe"], contexts, strict=True)):
            if not isinstance(asset, Mapping) or not isinstance(context, Mapping):
                raise ReaderError("HYPERLIQUID_METADATA_INVALID", "Invalid market metadata")
            name = asset.get("name")
            if isinstance(name, str):
                if name in by_name:
                    raise ReaderError("HYPERLIQUID_METADATA_INVALID", "Duplicate market metadata")
                by_name[name] = (index, asset, context)
        markets: list[dict[str, object]] = []
        for market in SUPPORTED_MARKETS:
            if market not in by_name:
                raise ReaderError("HYPERLIQUID_METADATA_INVALID", "Supported market is unavailable")
            index, asset, context = by_name[market]
            sz_decimals = asset.get("szDecimals")
            max_leverage = asset.get("maxLeverage")
            if type(sz_decimals) is not int or not 0 <= sz_decimals <= 8 or type(max_leverage) is not int or max_leverage < 3:
                raise ReaderError("HYPERLIQUID_METADATA_INVALID", "Invalid supported market limits")
            book = self._book(market)
            markets.append({
                "asset_index": index,
                "best_ask": book["best_ask"],
                "best_bid": book["best_bid"],
                "book_time_ms": book["book_time_ms"],
                "funding_rate": _number(context.get("funding"), "funding rate"),
                "market": market,
                "mark_price": _number(context.get("markPx"), "mark price", signed=False),
                "max_exchange_leverage": max_leverage,
                "open_interest_asset": _number(context.get("openInterest"), "open interest", signed=False),
                "oracle_price": _number(context.get("oraclePx"), "oracle price", signed=False),
                "supported": True,
                "sz_decimals": sz_decimals,
            })
        return {
            "code": "PERPDEX_MARKETS_READY",
            "markets": markets,
            "message": "Hyperliquid markets are available.",
            "observed_at": _observed_at(),
            "operation": "markets",
            "schema_version": READ_SCHEMA_VERSION,
            "status": "READY",
        }

    def _book(self, market: str) -> dict[str, object]:
        response = self._post({"coin": market, "nSigFigs": 5, "type": "l2Book"})
        if (
            not isinstance(response, Mapping)
            or response.get("coin") != market
            or type(response.get("time")) is not int
            or not isinstance(response.get("levels"), list)
            or len(response["levels"]) != 2
            or any(not isinstance(level, list) or not level for level in response["levels"])
        ):
            raise ReaderError("HYPERLIQUID_BOOK_INVALID", "Invalid order book")
        bid, ask = response["levels"][0][0], response["levels"][1][0]
        if not isinstance(bid, Mapping) or not isinstance(ask, Mapping):
            raise ReaderError("HYPERLIQUID_BOOK_INVALID", "Invalid order book")
        best_bid = _number(bid.get("px"), "best bid", signed=False)
        best_ask = _number(ask.get("px"), "best ask", signed=False)
        if Decimal(best_bid) <= 0 or Decimal(best_ask) <= Decimal(best_bid):
            raise ReaderError("HYPERLIQUID_BOOK_INVALID", "Crossed or empty order book")
        return {"best_ask": best_ask, "best_bid": best_bid, "book_time_ms": response["time"]}

    def portfolio(self, account: Mapping[str, str]) -> dict[str, object]:
        address = account["address"]
        state = self._post({"type": "clearinghouseState", "user": address})
        orders = self._post({"type": "frontendOpenOrders", "user": address})
        if not isinstance(state, Mapping) or not isinstance(orders, list):
            raise ReaderError("HYPERLIQUID_ACCOUNT_INVALID", "Invalid account state")
        summary = state.get("marginSummary")
        positions_value = state.get("assetPositions")
        if not isinstance(summary, Mapping) or not isinstance(positions_value, list):
            raise ReaderError("HYPERLIQUID_ACCOUNT_INVALID", "Invalid account state")
        positions = [self._position(item) for item in positions_value]
        normalized_orders = [self._order(item) for item in orders]
        return {
            "account": dict(account),
            "account_equity_usdc": _number(summary.get("accountValue"), "account equity", signed=True),
            "code": "PERPDEX_PORTFOLIO_READY",
            "margin_used_usdc": _number(summary.get("totalMarginUsed"), "margin used", signed=False),
            "message": "Hyperliquid portfolio is available.",
            "observed_at": _observed_at(),
            "operation": "portfolio",
            "orders": normalized_orders,
            "positions": positions,
            "schema_version": READ_SCHEMA_VERSION,
            "status": "READY",
            "total_notional_usdc": _number(summary.get("totalNtlPos"), "total notional", signed=False),
            "withdrawable_usdc": _number(state.get("withdrawable"), "withdrawable balance", signed=False),
        }

    @staticmethod
    def _position(item: object) -> dict[str, object]:
        if not isinstance(item, Mapping) or not isinstance(item.get("position"), Mapping):
            raise ReaderError("HYPERLIQUID_POSITION_INVALID", "Invalid position")
        position = item["position"]
        market = position.get("coin")
        leverage = position.get("leverage")
        if not isinstance(market, str) or not market or len(market) > 40 or not isinstance(leverage, Mapping):
            raise ReaderError("HYPERLIQUID_POSITION_INVALID", "Invalid position")
        size = _number(position.get("szi"), "position size")
        if Decimal(size) == 0:
            raise ReaderError("HYPERLIQUID_POSITION_INVALID", "Invalid zero position")
        leverage_type = leverage.get("type")
        leverage_value = leverage.get("value")
        if leverage_type not in {"cross", "isolated"} or type(leverage_value) is not int or leverage_value <= 0:
            raise ReaderError("HYPERLIQUID_POSITION_INVALID", "Invalid position leverage")
        return {
            "entry_price": _optional_number(position.get("entryPx"), "entry price", signed=False),
            "leverage_type": str(leverage_type).upper(),
            "leverage_value": leverage_value,
            "liquidation_price": _optional_number(position.get("liquidationPx"), "liquidation price", signed=False),
            "margin_used_usdc": _number(position.get("marginUsed"), "position margin", signed=False),
            "market": market,
            "position_notional_usdc": _number(position.get("positionValue"), "position notional", signed=False),
            "side": "LONG" if Decimal(size) > 0 else "SHORT",
            "size_asset": size,
            "supported": market in SUPPORTED_MARKETS,
            "unrealized_pnl_usdc": _number(position.get("unrealizedPnl"), "unrealized PnL"),
        }

    @staticmethod
    def _order(item: object) -> dict[str, object]:
        if not isinstance(item, Mapping):
            raise ReaderError("HYPERLIQUID_ORDER_INVALID", "Invalid order")
        market = item.get("coin")
        side_raw = item.get("side")
        if not isinstance(market, str) or not market or len(market) > 40 or side_raw not in {"A", "B", "Buy", "Sell"}:
            raise ReaderError("HYPERLIQUID_ORDER_INVALID", "Invalid order")
        oid = item.get("oid")
        if type(oid) is not int or oid < 0:
            raise ReaderError("HYPERLIQUID_ORDER_INVALID", "Invalid order id")
        reduce_only = item.get("reduceOnly", False)
        order_type = item.get("orderType")
        if type(reduce_only) is not bool or not isinstance(order_type, str) or not order_type:
            raise ReaderError("HYPERLIQUID_ORDER_INVALID", "Invalid order details")
        return {
            "limit_price": _number(item.get("limitPx"), "order limit price", signed=False),
            "market": market,
            "oid": str(oid),
            "order_type": order_type[:64],
            "reduce_only": reduce_only,
            "side": "BUY" if side_raw in {"B", "Buy"} else "SELL",
            "size_asset": _number(item.get("sz"), "order size", signed=False),
            "supported": market in SUPPORTED_MARKETS,
            "timestamp_ms": item.get("timestamp") if type(item.get("timestamp")) is int else None,
        }

    def fees(self, account: Mapping[str, str]) -> dict[str, object]:
        response = self._post({"type": "userFees", "user": account["address"]})
        if not isinstance(response, Mapping):
            raise ReaderError("HYPERLIQUID_FEES_INVALID", "Invalid fee state")
        return {
            "account": dict(account),
            "code": "PERPDEX_FEES_READY",
            "maker_rate": _number(response.get("userAddRate"), "maker fee rate"),
            "message": "Hyperliquid fees are available.",
            "observed_at": _observed_at(),
            "operation": "fees",
            "schema_version": READ_SCHEMA_VERSION,
            "status": "READY",
            "taker_rate": _number(response.get("userCrossRate"), "taker fee rate", signed=False),
        }

    def referral(self, account: Mapping[str, str]) -> dict[str, object]:
        response = self._post({"type": "referral", "user": account["address"]})
        if not isinstance(response, Mapping) or "referredBy" not in response:
            raise ReaderError("HYPERLIQUID_REFERRAL_INVALID", "Invalid referral state")
        referred_by = response["referredBy"]
        display: str | None
        if referred_by is None:
            display = None
        elif isinstance(referred_by, str) and referred_by.strip() and len(referred_by) <= 128:
            display = referred_by
        elif isinstance(referred_by, Mapping) and referred_by:
            candidate = referred_by.get("code") or referred_by.get("referrer")
            display = candidate if isinstance(candidate, str) and candidate and len(candidate) <= 128 else "assigned"
        else:
            raise ReaderError("HYPERLIQUID_REFERRAL_INVALID", "Invalid referral state")
        return {
            "account": dict(account),
            "code": "PERPDEX_REFERRAL_READY",
            "has_referrer": display is not None,
            "message": "Hyperliquid referral state is available.",
            "observed_at": _observed_at(),
            "operation": "referral",
            "referred_by": display,
            "schema_version": READ_SCHEMA_VERSION,
            "status": "READY",
        }

    def hlp(self, account: Mapping[str, str]) -> dict[str, object]:
        details = self._post({"type": "vaultDetails", "user": account["address"], "vaultAddress": HLP_ADDRESS})
        equities = self._post({"type": "userVaultEquities", "user": account["address"]})
        if not isinstance(details, Mapping) or not isinstance(equities, list):
            raise ReaderError("HLP_DATA_INVALID", "Invalid HLP state")
        relationship = details.get("relationship")
        if (
            str(details.get("vaultAddress", "")).lower() != HLP_ADDRESS
            or details.get("name") != HLP_NAME
            or not isinstance(relationship, Mapping)
            or relationship.get("type") != "parent"
            or type(details.get("allowDeposits")) is not bool
            or type(details.get("isClosed")) is not bool
        ):
            raise ReaderError("HLP_IDENTITY_MISMATCH", "Official HLP identity mismatch")
        equity = "0"
        for item in equities:
            if not isinstance(item, Mapping):
                raise ReaderError("HLP_DATA_INVALID", "Invalid HLP equity")
            if str(item.get("vaultAddress", "")).lower() == HLP_ADDRESS:
                equity = _number(item.get("equity"), "HLP equity", signed=False)
                break
        follower = details.get("followerState")
        pnl = all_time_pnl = "0"
        lockup_until_ms: int | None = None
        if follower is not None:
            if not isinstance(follower, Mapping):
                raise ReaderError("HLP_DATA_INVALID", "Invalid HLP follower state")
            follower_equity = _number(follower.get("vaultEquity"), "HLP follower equity", signed=False)
            if Decimal(equity) != 0 and Decimal(follower_equity) != Decimal(equity):
                raise ReaderError("HLP_DATA_INVALID", "Contradictory HLP equity")
            equity = follower_equity
            pnl = _number(follower.get("pnl"), "HLP PnL")
            all_time_pnl = _number(follower.get("allTimePnl"), "HLP all-time PnL")
            lockup = follower.get("lockupUntil")
            if type(lockup) is not int or lockup < 0:
                raise ReaderError("HLP_DATA_INVALID", "Invalid HLP lock-up")
            lockup_until_ms = lockup
        apr = _number(details.get("apr"), "HLP APR")
        apr_percent = _format_decimal(Decimal(apr) * 100)
        now_ms = int(datetime.now(UTC).timestamp() * 1000)
        unlocked = lockup_until_ms is None or lockup_until_ms <= now_ms
        return {
            "account": dict(account),
            "allow_deposits": details["allowDeposits"],
            "all_time_pnl_usdc": all_time_pnl,
            "code": "HLP_READY",
            "equity_usdc": equity,
            "is_closed": details["isClosed"],
            "lockup_until_ms": lockup_until_ms,
            "message": "Official HLP state is available.",
            "observed_at": _observed_at(),
            "operation": "hlp",
            "pnl_usdc": pnl,
            "protocol_apr_percent": apr_percent,
            "schema_version": READ_SCHEMA_VERSION,
            "status": "READY",
            "unlocked": unlocked,
            "withdrawable_equity_usdc": equity if unlocked else "0",
            "vault_address": HLP_ADDRESS,
            "vault_name": HLP_NAME,
        }
