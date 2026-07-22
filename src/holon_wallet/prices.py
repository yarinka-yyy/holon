"""Fail-closed read-only Chainlink prices and portfolio presentation helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP
from enum import Enum
from typing import Callable, Mapping, Protocol

from requests import exceptions as request_errors
from web3 import Web3

from .public_data import NetworkSnapshot, PublicDataStatus


BASE_CHAIN_ID = 8453
BASE_RPC_ENV = "HOLON_BASE_RPC_URL"
BASE_PUBLIC_RPC = "https://base-rpc.publicnode.com"
SEQUENCER_FEED = "0xBCF85224fc0756B9Fa45aA7892530B47e10b6433"
SEQUENCER_GRACE_SECONDS = 3_600

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
    prices: PriceSnapshot,
    selected_network: str,
) -> dict[str, object]:
    if selected_network not in {"all", "ethereum", "base"}:
        raise ValueError("Unsupported portfolio filter")
    selected_ids = (
        ("ethereum", "base") if selected_network == "all" else (selected_network,)
    )
    price_by_asset = prices.by_asset
    asset_models = tuple(
        _asset_model(asset_id, snapshots, price_by_asset, selected_ids)
        for asset_id in ("eth", "usdc")
    )
    network_models = tuple(
        _network_model(network_id, snapshots[network_id], price_by_asset)
        for network_id in ("ethereum", "base")
    )
    total_available = all(bool(asset["totalAvailable"]) for asset in asset_models)
    total = (
        sum((Decimal(str(asset["usdRaw"])) for asset in asset_models), Decimal(0))
        if total_available
        else None
    )
    return {
        "filter": selected_network,
        "totalAvailable": total_available,
        "totalUsd": format_usd(total) if total is not None else "$ —",
        "assets": list(asset_models),
        "networks": list(network_models),
    }


def estimate_wei_usd(maximum_fee_wei: int, prices: PriceSnapshot) -> str:
    eth = prices.by_asset.get("eth")
    if maximum_fee_wei < 0 or eth is None or eth.value is None:
        return "Data unavailable"
    value = Decimal(maximum_fee_wei).scaleb(-18) * eth.value
    return f"≈ {format_usd(value)}"


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
    prices: Mapping[str, AssetPrice],
    selected_ids: tuple[str, ...],
) -> dict[str, object]:
    symbol = "ETH" if asset_id == "eth" else "USDC"
    label = "Ethereum" if asset_id == "eth" else "USD Coin"
    decimals = 18 if asset_id == "eth" else 6
    breakdown: list[dict[str, object]] = []
    atomic_total = 0
    balances_available = True
    for network_id in ("ethereum", "base"):
        snapshot = snapshots[network_id]
        balance = snapshot.eth if asset_id == "eth" else snapshot.usdc
        available = (
            snapshot.status in {PublicDataStatus.LIVE, PublicDataStatus.SIMULATED}
            and balance is not None
        )
        atomic = balance.atomic_units if available and balance is not None else None
        if network_id in selected_ids:
            balances_available = balances_available and available
            if atomic is not None:
                atomic_total += atomic
        if network_id in selected_ids:
            breakdown.append(
                {
                    "networkId": network_id,
                    "label": snapshot.label,
                    "available": available,
                    "amount": (
                        _format_token(atomic, decimals, symbol)
                        if atomic is not None else "Data unavailable"
                    ),
                }
            )
    price = prices.get(asset_id)
    usd_available = balances_available and price is not None and price.value is not None
    usd = (
        Decimal(atomic_total).scaleb(-decimals) * price.value
        if usd_available and price is not None and price.value is not None
        else None
    )
    return {
        "assetId": asset_id,
        "symbol": symbol,
        "label": label,
        "balanceAvailable": balances_available,
        "amount": (
            _format_token(atomic_total, decimals, symbol)
            if balances_available else "Data unavailable"
        ),
        "totalAvailable": usd is not None,
        "usd": format_usd(usd) if usd is not None else "Data unavailable",
        "usdRaw": format(usd, "f") if usd is not None else "",
        "breakdown": breakdown,
    }


def _network_model(
    network_id: str,
    snapshot: NetworkSnapshot,
    prices: Mapping[str, AssetPrice],
) -> dict[str, object]:
    available = (
        snapshot.status in {PublicDataStatus.LIVE, PublicDataStatus.SIMULATED}
        and snapshot.eth is not None
        and snapshot.usdc is not None
    )
    eth_price = prices.get("eth")
    usdc_price = prices.get("usdc")
    total = None
    if (
        available
        and eth_price is not None
        and eth_price.value is not None
        and usdc_price is not None
        and usdc_price.value is not None
        and snapshot.eth is not None
        and snapshot.usdc is not None
    ):
        total = (
            Decimal(snapshot.eth.atomic_units).scaleb(-18) * eth_price.value
            + Decimal(snapshot.usdc.atomic_units).scaleb(-6) * usdc_price.value
        )
    return {
        "networkId": network_id,
        "label": snapshot.label,
        "status": snapshot.status.value,
        "totalAvailable": total is not None,
        "totalUsd": format_usd(total) if total is not None else "Data unavailable",
    }


def _format_token(value: int, decimals: int, symbol: str) -> str:
    decimal_value = Decimal(value).scaleb(-decimals)
    maximum_decimals = 6 if symbol == "ETH" else decimals
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
