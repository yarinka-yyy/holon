"""Fail-closed read-only Chainlink prices and portfolio presentation helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from enum import Enum
from typing import Callable, Mapping, Protocol

import requests
from requests import exceptions as request_errors
from web3 import Web3

from holon_contracts.registry import load_registry

from .public_data import NetworkSnapshot, PublicDataStatus


BASE_CHAIN_ID = 8453
BASE_RPC_ENV = "HOLON_BASE_RPC_URL"
BASE_PUBLIC_RPC = "https://mainnet.base.org"
SEQUENCER_FEED = "0xBCF85224fc0756B9Fa45aA7892530B47e10b6433"
SEQUENCER_GRACE_SECONDS = 3_600
BASE_HIGH_FEE_WARNING_USD = Decimal("0.05")
BASE_HIGH_FEE_WARNING_WEI = 20_000_000_000_000

AGGREGATOR_ABI = (
    {
        "inputs": (),
        "name": "decimals",
        "outputs": ({"name": "", "type": "uint8"},),
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": (),
        "name": "latestRoundData",
        "outputs": (
            {"name": "roundId", "type": "uint80"},
            {"name": "answer", "type": "int256"},
            {"name": "startedAt", "type": "uint256"},
            {"name": "updatedAt", "type": "uint256"},
            {"name": "answeredInRound", "type": "uint80"},
        ),
        "stateMutability": "view",
        "type": "function",
    },
)


class PriceStatus(str, Enum):
    LIVE = "LIVE"
    UNAVAILABLE = "UNAVAILABLE"


class MarketPriceStatus(str, Enum):
    LIVE = "LIVE"
    CACHED = "CACHED"
    UNAVAILABLE = "UNAVAILABLE"


@dataclass(frozen=True, slots=True)
class PriceFeedSpec:
    asset_id: str
    symbol: str
    label: str
    contract: str
    expected_decimals: int
    max_age_seconds: int


PRICE_FEEDS: tuple[PriceFeedSpec, ...] = (
    PriceFeedSpec(
        "eth",
        "ETH",
        "Ethereum",
        "0x71041dddad3595F9CEd3DcCFBe3D1F4b0a16Bb70",
        8,
        1_800,
    ),
    PriceFeedSpec(
        "usdc",
        "USDC",
        "USD Coin",
        "0x7e860098F58bBFC8648a4311b374B1D669a2bc6B",
        8,
        90_000,
    ),
)


@dataclass(frozen=True, slots=True)
class AssetPrice:
    asset_id: str
    symbol: str
    status: PriceStatus
    answer: int | None
    decimals: int | None
    updated_at: int | None
    error_code: str | None = None

    @property
    def value(self) -> Decimal | None:
        if (
            self.status is not PriceStatus.LIVE
            or self.answer is None
            or self.decimals is None
        ):
            return None
        return Decimal(self.answer).scaleb(-self.decimals)

    @classmethod
    def unavailable(cls, spec: PriceFeedSpec, code: str) -> AssetPrice:
        return cls(
            spec.asset_id,
            spec.symbol,
            PriceStatus.UNAVAILABLE,
            None,
            None,
            None,
            code,
        )


@dataclass(frozen=True, slots=True)
class PriceSnapshot:
    chain_id: int
    status: PriceStatus
    prices: tuple[AssetPrice, ...]
    observed_at: int
    error_code: str | None = None

    @property
    def by_asset(self) -> dict[str, AssetPrice]:
        return {price.asset_id: price for price in self.prices}

    @classmethod
    def unavailable(cls, now: int, code: str) -> PriceSnapshot:
        return cls(
            BASE_CHAIN_ID,
            PriceStatus.UNAVAILABLE,
            tuple(AssetPrice.unavailable(spec, code) for spec in PRICE_FEEDS),
            now,
            code,
        )


@dataclass(frozen=True, slots=True)
class MarketPrice:
    market_price_id: str
    coingecko_id: str
    status: MarketPriceStatus
    value_usd: Decimal | None
    updated_at: int | None
    error_code: str | None = None


@dataclass(frozen=True, slots=True)
class MarketPriceSnapshot:
    prices: tuple[MarketPrice, ...]
    observed_at: int
    error_code: str | None = None

    @property
    def by_market(self) -> dict[str, MarketPrice]:
        return {price.market_price_id: price for price in self.prices}

    @property
    def has_live(self) -> bool:
        return any(price.status is MarketPriceStatus.LIVE for price in self.prices)

    @property
    def has_cached(self) -> bool:
        return any(price.status is MarketPriceStatus.CACHED for price in self.prices)

    @classmethod
    def unavailable(cls, now: int, code: str) -> MarketPriceSnapshot:
        registry = load_registry()
        return cls(
            tuple(
                MarketPrice(
                    item.market_price_id, item.coingecko_id,
                    MarketPriceStatus.UNAVAILABLE, None, None, code,
                )
                for item in registry.market_prices
            ),
            now,
            code,
        )


class ChainlinkRpc(Protocol):
    def chain_id(self) -> int: ...

    def decimals(self, contract: str) -> int: ...

    def latest_round_data(self, contract: str) -> tuple[int, int, int, int, int]: ...


class Web3ChainlinkRpc:
    """Narrow provider surface: chain ID and fixed aggregator reads only."""

    def __init__(self, endpoint: str, timeout_seconds: float = 5.0) -> None:
        provider = Web3.HTTPProvider(
            endpoint,
            request_kwargs={"timeout": timeout_seconds},
            exception_retry_configuration=None,
        )
        self._web3 = Web3(provider)

    def chain_id(self) -> int:
        return int(self._web3.eth.chain_id)

    def decimals(self, contract: str) -> int:
        aggregator = self._web3.eth.contract(
            address=Web3.to_checksum_address(contract),
            abi=AGGREGATOR_ABI,
        )
        return int(aggregator.functions.decimals().call())

    def latest_round_data(self, contract: str) -> tuple[int, int, int, int, int]:
        aggregator = self._web3.eth.contract(
            address=Web3.to_checksum_address(contract),
            abi=AGGREGATOR_ABI,
        )
        values = aggregator.functions.latestRoundData().call()
        if len(values) != 5:
            raise ValueError("Invalid aggregator result")
        return tuple(int(value) for value in values)  # type: ignore[return-value]


RpcFactory = Callable[[str], ChainlinkRpc]
Clock = Callable[[], int]


class PriceService:
    """Reads fixed Base Chainlink feeds without persisting price data."""

    def __init__(
        self,
        rpc_factory: RpcFactory | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._rpc_factory = rpc_factory or Web3ChainlinkRpc
        self._environ = os.environ if environ is None else environ
        self._clock = clock or _utc_timestamp

    def refresh(self) -> PriceSnapshot:
        now = int(self._clock())
        endpoint = self._environ.get(BASE_RPC_ENV, BASE_PUBLIC_RPC).strip()
        if not endpoint:
            return PriceSnapshot.unavailable(now, "RPC_UNAVAILABLE")
        attempts = 0
        while True:
            try:
                rpc = self._rpc_factory(endpoint)
                if rpc.chain_id() != BASE_CHAIN_ID:
                    return PriceSnapshot.unavailable(now, "WRONG_CHAIN")
                sequencer_error = _validate_sequencer(rpc, now)
                if sequencer_error is not None:
                    return PriceSnapshot.unavailable(now, sequencer_error)
                prices = tuple(_read_price(rpc, spec, now) for spec in PRICE_FEEDS)
                status = (
                    PriceStatus.LIVE
                    if all(price.status is PriceStatus.LIVE for price in prices)
                    else PriceStatus.UNAVAILABLE
                )
                return PriceSnapshot(BASE_CHAIN_ID, status, prices, now)
            except _RETRYABLE_ERRORS:
                if attempts >= 1:
                    return PriceSnapshot.unavailable(now, "RPC_UNAVAILABLE")
                attempts += 1
            except (TypeError, ValueError, ArithmeticError):
                return PriceSnapshot.unavailable(now, "DATA_INVALID")
            except Exception:
                return PriceSnapshot.unavailable(now, "RPC_UNAVAILABLE")


class MarketHttpResponse(Protocol):
    status_code: int

    def json(self) -> object: ...


MarketHttpGet = Callable[..., MarketHttpResponse]
COINGECKO_SIMPLE_PRICE_URL = "https://api.coingecko.com/api/v3/simple/price"


class PortfolioMarketPriceService:
    """Reads pinned public market IDs without sending wallet information."""

    def __init__(
        self,
        http_get: MarketHttpGet | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._http_get = http_get or requests.get
        self._clock = clock or _utc_timestamp

    def refresh(self) -> MarketPriceSnapshot:
        registry = load_registry()
        now = int(self._clock())
        params = {
            "ids": ",".join(item.coingecko_id for item in registry.market_prices),
            "vs_currencies": "usd",
            "include_last_updated_at": "true",
        }
        for attempt in range(2):
            try:
                response = self._http_get(
                    COINGECKO_SIMPLE_PRICE_URL,
                    params=params,
                    timeout=5.0,
                )
                status_code = int(response.status_code)
                if status_code == 429 or 500 <= status_code <= 599:
                    if attempt == 0:
                        continue
                    return MarketPriceSnapshot.unavailable(now, "PRICE_SERVICE_UNAVAILABLE")
                if status_code != 200:
                    return MarketPriceSnapshot.unavailable(now, "PRICE_SERVICE_UNAVAILABLE")
                body = response.json()
                if not isinstance(body, dict):
                    return MarketPriceSnapshot.unavailable(now, "PRICE_DATA_INVALID")
                prices = tuple(
                    _read_market_price(body, item, now)
                    for item in registry.market_prices
                )
                return MarketPriceSnapshot(prices, now)
            except (request_errors.ConnectionError, request_errors.Timeout, TimeoutError):
                if attempt == 0:
                    continue
                return MarketPriceSnapshot.unavailable(now, "PRICE_SERVICE_UNAVAILABLE")
            except (TypeError, ValueError, ArithmeticError):
                return MarketPriceSnapshot.unavailable(now, "PRICE_DATA_INVALID")
            except Exception:
                return MarketPriceSnapshot.unavailable(now, "PRICE_SERVICE_UNAVAILABLE")
        return MarketPriceSnapshot.unavailable(now, "PRICE_SERVICE_UNAVAILABLE")


def market_snapshot_from_chainlink(snapshot: PriceSnapshot) -> MarketPriceSnapshot:
    """Compatibility bridge for old cache data and injected test services only."""
    registry = load_registry()
    legacy = snapshot.by_asset
    by_market = {"eth-usd": legacy.get("eth"), "usdc-usd": legacy.get("usdc")}
    prices: list[MarketPrice] = []
    for spec in registry.market_prices:
        old = by_market.get(spec.market_price_id)
        value = old.value if old is not None else None
        if value is not None:
            prices.append(MarketPrice(
                spec.market_price_id, spec.coingecko_id,
                MarketPriceStatus.LIVE, value, old.updated_at,
            ))
        else:
            prices.append(MarketPrice(
                spec.market_price_id, spec.coingecko_id,
                MarketPriceStatus.UNAVAILABLE, None, None,
                old.error_code if old is not None else "PRICE_UNAVAILABLE",
            ))
    return MarketPriceSnapshot(tuple(prices), snapshot.observed_at, snapshot.error_code)


def merge_market_price_snapshots(
    previous: MarketPriceSnapshot | None,
    fresh: MarketPriceSnapshot,
) -> MarketPriceSnapshot:
    registry = load_registry()
    old = previous.by_market if previous is not None else {}
    new = fresh.by_market
    merged: list[MarketPrice] = []
    for spec in registry.market_prices:
        current = new.get(spec.market_price_id)
        cached = old.get(spec.market_price_id)
        if current is not None and current.status is MarketPriceStatus.LIVE:
            merged.append(current)
        elif (
            cached is not None
            and cached.value_usd is not None
            and cached.updated_at is not None
        ):
            merged.append(MarketPrice(
                spec.market_price_id, spec.coingecko_id,
                MarketPriceStatus.CACHED, cached.value_usd, cached.updated_at,
                current.error_code if current is not None else "PRICE_UNAVAILABLE",
            ))
        elif current is not None:
            merged.append(current)
        else:
            merged.append(MarketPrice(
                spec.market_price_id, spec.coingecko_id,
                MarketPriceStatus.UNAVAILABLE, None, None, "PRICE_UNAVAILABLE",
            ))
    return MarketPriceSnapshot(tuple(merged), fresh.observed_at, fresh.error_code)


def _read_market_price(body: Mapping[str, object], spec: object, now: int) -> MarketPrice:
    market_price_id = str(getattr(spec, "market_price_id"))
    coingecko_id = str(getattr(spec, "coingecko_id"))
    raw = body.get(coingecko_id)
    if not isinstance(raw, Mapping):
        return MarketPrice(
            market_price_id, coingecko_id, MarketPriceStatus.UNAVAILABLE,
            None, None, "PRICE_UNAVAILABLE",
        )
    try:
        value = Decimal(str(raw.get("usd")))
    except (ArithmeticError, ValueError):
        return MarketPrice(
            market_price_id, coingecko_id, MarketPriceStatus.UNAVAILABLE,
            None, None, "PRICE_INVALID",
        )
    updated_at = raw.get("last_updated_at")
    max_age_seconds = int(getattr(spec, "max_age_seconds"))
    if (
        not value.is_finite()
        or value <= 0
        or type(updated_at) is not int
        or updated_at <= 0
        or updated_at > now
        or now - updated_at > max_age_seconds
    ):
        return MarketPrice(
            market_price_id, coingecko_id, MarketPriceStatus.UNAVAILABLE,
            None, None, "PRICE_INVALID",
        )
    return MarketPrice(
        market_price_id, coingecko_id, MarketPriceStatus.LIVE,
        value, updated_at,
    )


def price_snapshot_to_map(snapshot: PriceSnapshot) -> dict[str, object]:
    prices = snapshot.by_asset
    return {
        "status": snapshot.status.value,
        "observedAt": str(snapshot.observed_at),
        "ethUsd": _price_text(prices.get("eth")),
        "usdcUsd": _price_text(prices.get("usdc")),
        "ethStatus": _price_status(prices.get("eth")),
        "usdcStatus": _price_status(prices.get("usdc")),
        "errorCode": snapshot.error_code or "",
    }


def portfolio_to_map(
    snapshots: Mapping[str, NetworkSnapshot],
    prices: MarketPriceSnapshot | PriceSnapshot,
    selected_network: str,
    lending_protocols: object = None,
) -> dict[str, object]:
    registry = load_registry()
    network_ids = tuple(item.network_id for item in registry.networks)
    if selected_network not in {"all", *network_ids}:
        raise ValueError("Unsupported portfolio filter")
    selected_ids = (
        tuple(item for item in network_ids if item in snapshots)
        if selected_network == "all" else (selected_network,)
    )
    market_prices = (
        market_snapshot_from_chainlink(prices)
        if isinstance(prices, PriceSnapshot) else prices
    )
    price_by_market = market_prices.by_market
    wallet_assets = tuple(
        _asset_model(
            asset.asset_id, snapshots, price_by_market, selected_ids,
            selected_network != "all"
            and registry.network_by_id[selected_network].native_asset_id == asset.asset_id,
            position,
        )
        for position, asset in enumerate(registry.assets)
        if any(
            deployment.asset_id == asset.asset_id
            and deployment.network_id in selected_ids
            for deployment in registry.deployments
        )
    )
    wallet_assets = tuple(sorted(
        wallet_assets,
        key=lambda item: _asset_sort_key(item, selected_network != "all"),
    ))
    lending_items = (
        list(lending_protocols) if isinstance(lending_protocols, (list, tuple))
        else []
    )
    lending_included = "base" in selected_ids and lending_protocols is not None
    known_lending_complete = (
        len(lending_items) == 3
        and all(
            isinstance(item, Mapping)
            and isinstance(item.get("position_atomic"), str)
            and item["position_atomic"].isdecimal()
            for item in lending_items
        )
    )
    lending_complete = not lending_included or known_lending_complete
    lending_assets = tuple(
        _lending_asset_model(item, price_by_market)
        for item in lending_items
        if lending_included
        and isinstance(item, Mapping)
        and isinstance(item.get("position_atomic"), str)
        and item["position_atomic"].isdecimal()
        and int(item["position_atomic"]) > 0
    )
    asset_models = wallet_assets + lending_assets
    all_lending_total = (
        sum(int(item["position_atomic"]) for item in lending_items)
        if known_lending_complete else None
    )
    network_models = tuple(
        _network_model(
            network_id, snapshots[network_id], price_by_market,
            all_lending_total if network_id == "base" and lending_protocols is not None else 0,
        )
        for network_id in selected_ids
    )
    total_available = (
        lending_complete
        and all(bool(asset["totalAvailable"]) for asset in asset_models)
    )
    total = (
        sum((Decimal(str(asset["usdRaw"])) for asset in asset_models), Decimal(0))
        if total_available
        else None
    )
    visible_assets = [
        {key: value for key, value in asset.items() if not key.startswith("_")}
        for asset in asset_models
    ]
    return {
        "filter": selected_network,
        "totalAvailable": total_available,
        "totalUsd": format_usd(total) if total is not None else "$ —",
        "assets": visible_assets,
        "networks": list(network_models),
        "lendingComplete": lending_complete,
    }


def estimate_wei_usd(maximum_fee_wei: int, prices: PriceSnapshot) -> str:
    eth = prices.by_asset.get("eth")
    if maximum_fee_wei < 0 or eth is None or eth.value is None:
        return "Data unavailable"
    value = Decimal(maximum_fee_wei).scaleb(-18) * eth.value
    return f"≈ {format_usd(value)}"


def is_unusually_high_base_fee(
    maximum_fee_wei: int, prices: PriceSnapshot,
) -> bool:
    """Soft Base warning only; it never grants or refuses signing authority."""
    if maximum_fee_wei <= 0:
        return False
    eth = prices.by_asset.get("eth")
    if eth is None or eth.value is None:
        return maximum_fee_wei >= BASE_HIGH_FEE_WARNING_WEI
    value = Decimal(maximum_fee_wei).scaleb(-18) * eth.value
    return value >= BASE_HIGH_FEE_WARNING_USD


def estimate_asset_usd(
    atomic_units: int,
    decimals: int,
    asset_id: str,
    prices: PriceSnapshot,
) -> str:
    price = prices.by_asset.get(asset_id)
    if atomic_units < 0 or decimals < 0 or price is None or price.value is None:
        return "Data unavailable"
    value = Decimal(atomic_units).scaleb(-decimals) * price.value
    return f"≈ {format_usd(value)}"


def format_usd(value: Decimal) -> str:
    rounded = value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return f"${rounded:,.2f}"


def _validate_sequencer(rpc: ChainlinkRpc, now: int) -> str | None:
    decimals = rpc.decimals(SEQUENCER_FEED)
    round_id, answer, started_at, updated_at, answered_in_round = (
        rpc.latest_round_data(SEQUENCER_FEED)
    )
    if decimals != 0 or round_id <= 0 or answered_in_round < round_id:
        return "SEQUENCER_DATA_INVALID"
    if answer != 0:
        return "SEQUENCER_DOWN"
    if started_at <= 0 or updated_at <= 0 or started_at > now or updated_at > now:
        return "SEQUENCER_DATA_INVALID"
    if now - started_at <= SEQUENCER_GRACE_SECONDS:
        return "SEQUENCER_GRACE_PERIOD"
    return None


def _read_price(rpc: ChainlinkRpc, spec: PriceFeedSpec, now: int) -> AssetPrice:
    decimals = rpc.decimals(spec.contract)
    round_id, answer, started_at, updated_at, answered_in_round = (
        rpc.latest_round_data(spec.contract)
    )
    if (
        decimals != spec.expected_decimals
        or round_id <= 0
        or answered_in_round < round_id
        or answer <= 0
        or started_at <= 0
        or updated_at <= 0
        or started_at > updated_at
        or updated_at > now
        or now - updated_at > spec.max_age_seconds
    ):
        return AssetPrice.unavailable(spec, "PRICE_INVALID")
    return AssetPrice(
        spec.asset_id,
        spec.symbol,
        PriceStatus.LIVE,
        answer,
        decimals,
        updated_at,
    )


def _asset_model(
    asset_id: str,
    snapshots: Mapping[str, NetworkSnapshot],
    prices: Mapping[str, MarketPrice],
    selected_ids: tuple[str, ...],
    is_gas_asset: bool,
    registry_position: int,
) -> dict[str, object]:
    registry = load_registry()
    meta = registry.asset_by_id[asset_id]
    symbol, label, decimals = meta.display_symbol, meta.display_name, meta.decimals
    breakdown: list[dict[str, object]] = []
    atomic_total = 0
    known_balances = 0
    balances_available = True
    incomplete = False
    deployments = {
        item.network_id for item in registry.deployments
        if item.asset_id == asset_id and item.network_id in selected_ids
    }
    for network_id in selected_ids:
        if network_id not in deployments:
            continue
        snapshot = snapshots.get(network_id)
        balance = snapshot.assets_by_id.get(asset_id) if snapshot is not None else None
        available = (
            snapshot is not None
            and snapshot.status in {
                PublicDataStatus.LIVE, PublicDataStatus.PARTIAL,
                PublicDataStatus.SIMULATED,
            }
            and balance is not None
        )
        atomic = balance.atomic_units if available and balance is not None else None
        balances_available = balances_available and available
        stale = snapshot is not None and asset_id in snapshot.errors_by_id
        incomplete = incomplete or stale or not available
        if atomic is not None:
            atomic_total += atomic
            known_balances += 1
        breakdown.append({
            "networkId": network_id,
            "label": snapshot.label if snapshot is not None else registry.network_by_id[network_id].display_name,
            "available": available,
            "stale": stale,
            "amount": _format_token(atomic, decimals, symbol) if atomic is not None else "Data unavailable",
        })
    price = prices.get(meta.market_price_id) if meta.market_price_id else None
    price_available = price is not None and price.value_usd is not None
    usd = (
        Decimal(atomic_total).scaleb(-decimals) * price.value_usd
        if known_balances and price_available
        and price is not None and price.value_usd is not None
        else None
    )
    contribution_available = balances_available and (price_available or atomic_total == 0)
    if balances_available and atomic_total == 0:
        usd_text = "$0.00"
    elif usd is not None:
        usd_text = format_usd(usd)
    else:
        usd_text = "Data unavailable"
    return {
        "assetId": asset_id,
        "isGasAsset": is_gas_asset,
        "symbol": symbol,
        "label": label,
        "balanceAvailable": balances_available,
        "amount": _format_token(atomic_total, decimals, symbol) if known_balances else "Data unavailable",
        "incomplete": incomplete,
        "totalAvailable": contribution_available,
        "usd": usd_text,
        "usdRaw": format(usd, "f") if usd is not None else "0" if contribution_available else "",
        "breakdown": breakdown,
        "iconSource": meta.icon_path.removeprefix("qml/"),
        "iconVisualSize": meta.icon_visual_size,
        "_atomicRaw": str(atomic_total),
        "_registryPosition": registry_position,
    }


def _network_model(
    network_id: str,
    snapshot: NetworkSnapshot,
    prices: Mapping[str, MarketPrice],
    lending_atomic: int | None = 0,
) -> dict[str, object]:
    registry = load_registry()
    total = Decimal(0)
    available = snapshot.status in {
        PublicDataStatus.LIVE, PublicDataStatus.PARTIAL, PublicDataStatus.SIMULATED,
    }
    by_id = snapshot.assets_by_id
    for deployment in registry.deployments_by_network[network_id]:
        meta = registry.asset_by_id[deployment.asset_id]
        balance = by_id.get(deployment.asset_id)
        if balance is None:
            available = False
            continue
        price = prices.get(meta.market_price_id) if meta.market_price_id else None
        if price is not None and price.value_usd is not None:
            total += Decimal(balance.atomic_units).scaleb(-meta.decimals) * price.value_usd
        elif balance.atomic_units:
            available = False
    usdc_price = prices.get("usdc-usd")
    if lending_atomic is None:
        available = False
    elif lending_atomic:
        if usdc_price is None or usdc_price.value_usd is None:
            available = False
        else:
            total += Decimal(lending_atomic).scaleb(-6) * usdc_price.value_usd
    return {
        "networkId": network_id,
        "label": snapshot.label,
        "status": snapshot.status.value,
        "totalAvailable": available,
        "totalUsd": format_usd(total) if available else "Data unavailable",
    }


def _lending_asset_model(
    value: Mapping[str, object], prices: Mapping[str, MarketPrice],
) -> dict[str, object]:
    atomic = int(str(value["position_atomic"]))
    price = prices.get("usdc-usd")
    usd = (
        Decimal(atomic).scaleb(-6) * price.value_usd
        if price is not None and price.value_usd is not None else None
    )
    protocol = str(value.get("protocol", ""))
    label = {
        "aave-v3": "Aave V3",
        "compound-v3": "Compound III",
        "morpho-v1": "Morpho V1",
    }.get(protocol, str(value.get("display_name", "Lending")))
    return {
        "assetId": protocol,
        "isGasAsset": False,
        "symbol": "USDC · Base Lending",
        "label": label,
        "balanceAvailable": True,
        "amount": _format_token(atomic, 6, "USDC"),
        "totalAvailable": usd is not None,
        "usd": format_usd(usd) if usd is not None else "Data unavailable",
        "usdRaw": format(usd, "f") if usd is not None else "",
        "breakdown": [],
        "dataState": str(value.get("data_state", "UNAVAILABLE")),
        "iconSource": {
            "aave-v3": "assets/aave-logo-white.png",
            "compound-v3": "assets/compound-logo-white.svg",
            "morpho-v1": "assets/morpho-logo-white.svg",
        }.get(protocol, "assets/usdc.webp"),
    }


def _asset_sort_key(
    value: Mapping[str, object], gas_first: bool,
) -> tuple[int, int, Decimal, int]:
    """Rank a filtered network by gas then USD; rank All Networks only by USD."""
    position = int(value.get("_registryPosition", 0))
    gas_rank = 0 if gas_first and value.get("isGasAsset") is True else 1
    balance_available = value.get("balanceAvailable") is True
    try:
        atomic = int(str(value.get("_atomicRaw", "0")))
    except ValueError:
        atomic = 0
    raw_usd = value.get("usdRaw")
    try:
        usd = Decimal(str(raw_usd)) if raw_usd not in {None, ""} else None
    except (ArithmeticError, ValueError):
        usd = None
    if not balance_available:
        category = 2
    elif atomic == 0:
        category = 3
    elif usd is not None:
        category = 0
    else:
        category = 1
    return gas_rank, category, -usd if usd is not None else Decimal(0), position


def _format_token(value: int, decimals: int, symbol: str) -> str:
    decimal_value = Decimal(value).scaleb(-decimals)
    if symbol == "USDC":
        rounded = decimal_value.quantize(Decimal("0.01"), rounding=ROUND_DOWN)
        if rounded == 0:
            rounded = abs(rounded)
        return f"{rounded:.2f} {symbol}"
    maximum_decimals = 6
    quantum = Decimal(1).scaleb(-maximum_decimals)
    if decimal_value:
        decimal_value = decimal_value.quantize(quantum, rounding=ROUND_DOWN)
    rendered = format(decimal_value, "f").rstrip("0").rstrip(".")
    return f"{rendered or '0'} {symbol}"


def _price_text(price: AssetPrice | None) -> str:
    return format(price.value, "f") if price is not None and price.value is not None else ""


def _price_status(price: AssetPrice | None) -> str:
    return price.status.value if price is not None else PriceStatus.UNAVAILABLE.value


def _utc_timestamp() -> int:
    return int(datetime.now(UTC).timestamp())


_RETRYABLE_ERRORS = (
    request_errors.ConnectionError,
    request_errors.Timeout,
    request_errors.HTTPError,
    TimeoutError,
)
