"""Strict public-only v3 cache with safe v1/v2 migration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Mapping

from holon_contracts.registry import load_registry

from .prices import (
    AssetPrice,
    MarketPrice,
    MarketPriceSnapshot,
    MarketPriceStatus,
    PriceSnapshot,
    PriceStatus,
    market_snapshot_from_chainlink,
    merge_market_price_snapshots,
)
from .public_data import (
    NETWORK_BY_ID, AssetBalance, AssetReadError, NetworkSnapshot, PublicDataStatus,
)
from .storage import StorageError, WalletPaths, atomic_write_json, read_json


CACHE_SCHEMA_VERSION = 3
MAX_CACHED_PROFILES = 50


@dataclass(frozen=True, slots=True)
class CachedPublicBundle:
    profile_id: str
    address: str
    networks: tuple[NetworkSnapshot, ...]
    market_prices: MarketPriceSnapshot
    saved_at: str


class PublicCacheStore:
    """Persists public balances separately from prices and never grants authority."""

    def __init__(self, paths: WalletPaths) -> None:
        self.path = paths.public_cache

    def load(self, profile_id: str, address: str) -> CachedPublicBundle | None:
        try:
            profiles, _schema = self._load_profiles()
            value = next(
                item for item in profiles
                if item.get("profile_id") == profile_id and item.get("address") == address
            )
            return _bundle_from_dict(value)
        except (StopIteration, StorageError, TypeError, ValueError, ArithmeticError):
            return None

    def save(
        self, profile_id: str, address: str,
        networks: Mapping[str, NetworkSnapshot],
        prices: MarketPriceSnapshot | PriceSnapshot,
    ) -> None:
        fresh = tuple(
            item for network_id in NETWORK_BY_ID
            if (item := networks.get(network_id)) is not None
            and item.status in {PublicDataStatus.LIVE, PublicDataStatus.PARTIAL}
            and item.assets
        )
        try:
            profiles, _schema = self._load_profiles()
        except (StorageError, TypeError, ValueError, ArithmeticError):
            profiles = []
        previous_value = next((
            item for item in profiles
            if item.get("profile_id") == profile_id and item.get("address") == address
        ), None)
        previous = None
        try:
            previous = _bundle_from_dict(previous_value) if previous_value else None
        except (TypeError, ValueError, ArithmeticError):
            previous = None
        if not fresh and previous is None:
            return
        merged = _merge_networks(previous.networks if previous else (), fresh)
        market_prices = (
            market_snapshot_from_chainlink(prices)
            if isinstance(prices, PriceSnapshot) else prices
        )
        cached_prices = merge_market_price_snapshots(
            previous.market_prices if previous is not None else None,
            market_prices,
        )
        saved_at = _utc_now()
        candidate = _bundle_to_dict(CachedPublicBundle(
            profile_id, address, merged, cached_prices, saved_at,
        ))
        retained = [
            item for item in profiles
            if not (item.get("profile_id") == profile_id and item.get("address") == address)
        ][-(MAX_CACHED_PROFILES - 1):]
        retained.append(candidate)
        atomic_write_json(
            self.path, {"schema_version": CACHE_SCHEMA_VERSION, "profiles": retained},
        )

    def _load_profiles(self) -> tuple[list[dict[str, object]], int]:
        if not self.path.exists():
            return [], CACHE_SCHEMA_VERSION
        value = read_json(self.path)
        if (
            not isinstance(value, dict) or set(value) != {"schema_version", "profiles"}
            or value.get("schema_version") not in {1, 2, 3}
            or not isinstance(value.get("profiles"), list)
            or len(value["profiles"]) > MAX_CACHED_PROFILES
            or any(not isinstance(item, dict) for item in value["profiles"])
        ):
            raise ValueError("Public cache is invalid")
        schema = int(value["schema_version"])
        profiles = list(value["profiles"])
        if schema == 1:
            return (
                [_migrate_v2_profile(_migrate_v1_profile(item)) for item in profiles],
                schema,
            )
        if schema == 2:
            return ([_migrate_v2_profile(item) for item in profiles], schema)
        return profiles, schema


def _merge_networks(
    previous: tuple[NetworkSnapshot, ...], fresh: tuple[NetworkSnapshot, ...],
) -> tuple[NetworkSnapshot, ...]:
    old = {item.network_id: item for item in previous}
    new = {item.network_id: item for item in fresh}
    merged: list[NetworkSnapshot] = []
    for network_id, spec in NETWORK_BY_ID.items():
        current, cached = new.get(network_id), old.get(network_id)
        if current is None and cached is not None:
            merged.append(cached)
            continue
        if current is None:
            continue
        by_id = cached.assets_by_id.copy() if cached is not None else {}
        by_id.update({
            asset_id: AssetBalance(
                asset.symbol, asset.atomic_units, asset.decimals,
                asset.asset_id, current.updated_at or asset.updated_at,
            )
            for asset_id, asset in current.assets_by_id.items()
        })
        ordered = tuple(by_id[item.asset_id] for item in spec.assets if item.asset_id in by_id)
        missing = tuple(
            AssetReadError(item.asset_id, "DATA_UNAVAILABLE")
            for item in spec.assets if item.asset_id not in by_id
        )
        merged.append(NetworkSnapshot(
            network_id, spec.label, spec.chain_id,
            PublicDataStatus.PARTIAL if missing else PublicDataStatus.LIVE,
            current.block_number or (cached.block_number if cached else None),
            None, None, current.updated_at or (cached.updated_at if cached else None),
            "ASSET_DATA_UNAVAILABLE" if missing else None, ordered, missing,
        ))
    return tuple(merged)


def _bundle_to_dict(bundle: CachedPublicBundle) -> dict[str, object]:
    return {
        "profile_id": bundle.profile_id,
        "address": bundle.address,
        "saved_at": bundle.saved_at,
        "balances": {
            "networks": [
                {
                    "network_id": item.network_id,
                    "chain_id": item.chain_id,
                    "block_number": item.block_number,
                    "assets": [
                        {
                            "asset_id": asset.asset_id,
                            "atomic": str(asset.atomic_units),
                            "updated_at": asset.updated_at or item.updated_at,
                        }
                        for asset in item.assets
                    ],
                }
                for item in bundle.networks if item.block_number is not None and item.updated_at
            ]
        },
        "market_prices": _market_prices_to_dict(bundle.market_prices),
    }


def _bundle_from_dict(value: object) -> CachedPublicBundle:
    if not isinstance(value, dict) or set(value) != {
        "profile_id", "address", "saved_at", "balances", "market_prices",
    }:
        raise ValueError("Public cache profile is invalid")
    profile_id, address, saved_at = value["profile_id"], value["address"], value["saved_at"]
    if not all(isinstance(item, str) and item for item in (profile_id, address, saved_at)):
        raise ValueError("Public cache identity is invalid")
    _parse_time(saved_at)
    balances = value["balances"]
    if not isinstance(balances, dict) or set(balances) != {"networks"} or not isinstance(balances["networks"], list):
        raise ValueError("Public cache balances are invalid")
    networks = tuple(_network_from_dict(item) for item in balances["networks"])
    if not networks or len(networks) > len(NETWORK_BY_ID) or len({item.network_id for item in networks}) != len(networks):
        raise ValueError("Public cache networks are invalid")
    return CachedPublicBundle(
        profile_id, address, networks,
        _market_prices_from_dict(value["market_prices"]), saved_at,
    )


def _network_from_dict(value: object) -> NetworkSnapshot:
    if not isinstance(value, dict) or set(value) != {"network_id", "chain_id", "block_number", "assets"}:
        raise ValueError("Public cache network is invalid")
    network_id = value["network_id"]
    if network_id not in NETWORK_BY_ID:
        raise ValueError("Public cache network is unsupported")
    spec = NETWORK_BY_ID[network_id]
    if value["chain_id"] != spec.chain_id:
        raise ValueError("Public cache chain is invalid")
    block = _positive_int(value["block_number"])
    raw_assets = value["assets"]
    if not isinstance(raw_assets, list) or not raw_assets or len(raw_assets) > len(spec.assets):
        raise ValueError("Public cache assets are invalid")
    expected = {item.asset_id: item for item in spec.assets}
    assets: list[AssetBalance] = []
    timestamps: list[str] = []
    for raw in raw_assets:
        if not isinstance(raw, dict) or set(raw) != {"asset_id", "atomic", "updated_at"}:
            raise ValueError("Public cache asset is invalid")
        asset_id = raw["asset_id"]
        if asset_id not in expected or any(item.asset_id == asset_id for item in assets):
            raise ValueError("Public cache asset identity is invalid")
        updated = raw["updated_at"]
        if not isinstance(updated, str):
            raise ValueError("Public cache timestamp is invalid")
        _parse_time(updated)
        timestamps.append(updated)
        meta = expected[asset_id]
        assets.append(AssetBalance(
            meta.symbol, _atomic(raw["atomic"]), meta.decimals,
            meta.asset_id, updated,
        ))
    ordered = tuple(next(item for item in assets if item.asset_id == meta.asset_id) for meta in spec.assets if any(item.asset_id == meta.asset_id for item in assets))
    missing = tuple(AssetReadError(item.asset_id, "DATA_UNAVAILABLE") for item in spec.assets if not any(asset.asset_id == item.asset_id for asset in assets))
    return NetworkSnapshot(
        network_id, spec.label, spec.chain_id,
        PublicDataStatus.PARTIAL if missing else PublicDataStatus.LIVE,
        block, None, None, max(timestamps),
        "ASSET_DATA_UNAVAILABLE" if missing else None, ordered, missing,
    )


def _market_prices_to_dict(value: MarketPriceSnapshot) -> dict[str, object]:
    return {
        "observed_at": value.observed_at,
        "error_code": value.error_code,
        "assets": [
            {
                "market_price_id": item.market_price_id,
                "coingecko_id": item.coingecko_id,
                "status": item.status.value,
                "value_usd": (
                    format(item.value_usd, "f")
                    if item.value_usd is not None else None
                ),
                "updated_at": item.updated_at,
                "error_code": item.error_code,
            }
            for item in value.prices
        ],
    }


def _market_prices_from_dict(value: object) -> MarketPriceSnapshot:
    if (
        not isinstance(value, dict)
        or set(value) != {"observed_at", "error_code", "assets"}
    ):
        raise ValueError("Public cache market prices are invalid")
    observed_at = _positive_int(value["observed_at"])
    if value["error_code"] is not None and not isinstance(value["error_code"], str):
        raise ValueError("Public cache market price error is invalid")
    assets = value["assets"]
    registry = load_registry()
    if not isinstance(assets, list) or len(assets) != len(registry.market_prices):
        raise ValueError("Public cache market price assets are invalid")
    parsed = tuple(_market_price_from_dict(item) for item in assets)
    if tuple(item.market_price_id for item in parsed) != tuple(
        item.market_price_id for item in registry.market_prices
    ):
        raise ValueError("Public cache market price order is invalid")
    return MarketPriceSnapshot(parsed, observed_at, value["error_code"])


def _market_price_from_dict(value: object) -> MarketPrice:
    if not isinstance(value, dict) or set(value) != {
        "market_price_id", "coingecko_id", "status", "value_usd",
        "updated_at", "error_code",
    }:
        raise ValueError("Public cache market price is invalid")
    registry = load_registry()
    market_price_id = value["market_price_id"]
    if market_price_id not in registry.market_price_by_id:
        raise ValueError("Public cache market price identity is invalid")
    spec = registry.market_price_by_id[market_price_id]
    if (
        value["coingecko_id"] != spec.coingecko_id
        or value["status"] not in {item.value for item in MarketPriceStatus}
    ):
        raise ValueError("Public cache market price identity is invalid")
    status = MarketPriceStatus(value["status"])
    if value["error_code"] is not None and not isinstance(value["error_code"], str):
        raise ValueError("Public cache market price error is invalid")
    if status is MarketPriceStatus.UNAVAILABLE:
        if (
            value["value_usd"] is not None
            or value["updated_at"] is not None
            or not isinstance(value["error_code"], str)
        ):
            raise ValueError("Public cache unavailable market price is invalid")
        return MarketPrice(
            spec.market_price_id, spec.coingecko_id, status,
            None, None, value["error_code"],
        )
    raw_value = value["value_usd"]
    if not isinstance(raw_value, str):
        raise ValueError("Public cache market price value is invalid")
    amount = Decimal(raw_value)
    if not amount.is_finite() or amount <= 0:
        raise ValueError("Public cache market price value is invalid")
    return MarketPrice(
        spec.market_price_id, spec.coingecko_id, status, amount,
        _positive_int(value["updated_at"]), value["error_code"],
    )


def _prices_to_dict(value: PriceSnapshot) -> dict[str, object]:
    return {
        "chain_id": value.chain_id,
        "status": value.status.value,
        "observed_at": value.observed_at,
        "error_code": value.error_code,
        "assets": [
            {
                "asset_id": item.asset_id, "symbol": item.symbol,
                "status": item.status.value,
                "answer": item.answer, "decimals": item.decimals,
                "updated_at": item.updated_at, "error_code": item.error_code,
            }
            for item in value.prices
        ],
    }


def _prices_from_dict(value: object) -> PriceSnapshot:
    if not isinstance(value, dict) or set(value) != {"chain_id", "status", "observed_at", "error_code", "assets"}:
        raise ValueError("Public cache prices are invalid")
    if value["chain_id"] != 8453 or value["status"] not in {item.value for item in PriceStatus}:
        raise ValueError("Public cache prices are invalid")
    observed = _positive_int(value["observed_at"])
    raw_assets = value["assets"]
    if not isinstance(raw_assets, list) or len(raw_assets) != 2:
        raise ValueError("Public cache price assets are invalid")
    assets = tuple(_price_from_dict(item) for item in raw_assets)
    if {item.asset_id for item in assets} != {"eth", "usdc"}:
        raise ValueError("Public cache price assets are invalid")
    return PriceSnapshot(8453, PriceStatus(value["status"]), assets, observed, value["error_code"])


def _price_from_dict(value: object) -> AssetPrice:
    if not isinstance(value, dict) or set(value) != {"asset_id", "symbol", "status", "answer", "decimals", "updated_at", "error_code"}:
        raise ValueError("Public cache price is invalid")
    asset_id = value["asset_id"]
    symbol = {"eth": "ETH", "usdc": "USDC"}.get(asset_id)
    if value["symbol"] != symbol or value["status"] not in {item.value for item in PriceStatus}:
        raise ValueError("Public cache price identity is invalid")
    status = PriceStatus(value["status"])
    if status is PriceStatus.UNAVAILABLE:
        if any(value[name] is not None for name in ("answer", "decimals", "updated_at")) or not isinstance(value["error_code"], str):
            raise ValueError("Public cache unavailable price is invalid")
        return AssetPrice(str(asset_id), str(symbol), status, None, None, None, value["error_code"])
    return AssetPrice(
        str(asset_id), str(symbol), status,
        _positive_int(value["answer"]), _positive_int(value["decimals"], allow_zero=True),
        _positive_int(value["updated_at"]), value["error_code"],
    )


def _migrate_v1_profile(value: dict[str, object]) -> dict[str, object]:
    if set(value) != {"profile_id", "address", "saved_at", "networks", "prices"}:
        raise ValueError("Public cache v1 profile is invalid")
    networks = value["networks"]
    if not isinstance(networks, list):
        raise ValueError("Public cache v1 networks are invalid")
    migrated: list[dict[str, object]] = []
    for item in networks:
        if not isinstance(item, dict) or set(item) != {"network_id", "chain_id", "block_number", "updated_at", "eth_atomic", "usdc_atomic"}:
            raise ValueError("Public cache v1 network is invalid")
        migrated.append({
            "network_id": item["network_id"], "chain_id": item["chain_id"],
            "block_number": item["block_number"],
            "assets": [
                {"asset_id": "eth", "atomic": item["eth_atomic"], "updated_at": item["updated_at"]},
                {"asset_id": "usdc", "atomic": item["usdc_atomic"], "updated_at": item["updated_at"]},
            ],
        })
    old_prices = value["prices"]
    if not isinstance(old_prices, dict):
        raise ValueError("Public cache v1 prices are invalid")
    prices = {
        "chain_id": old_prices.get("chain_id"), "status": "LIVE",
        "observed_at": old_prices.get("observed_at"), "error_code": None,
        "assets": [
            {**item, "status": "LIVE", "error_code": None}
            for item in old_prices.get("assets", []) if isinstance(item, dict)
        ],
    }
    return {
        "profile_id": value["profile_id"], "address": value["address"],
        "saved_at": value["saved_at"], "balances": {"networks": migrated},
        "prices": prices,
    }


def _migrate_v2_profile(value: dict[str, object]) -> dict[str, object]:
    if set(value) != {
        "profile_id", "address", "saved_at", "balances", "prices",
    }:
        raise ValueError("Public cache v2 profile is invalid")
    legacy = _prices_from_dict(value["prices"])
    return {
        "profile_id": value["profile_id"],
        "address": value["address"],
        "saved_at": value["saved_at"],
        "balances": value["balances"],
        "market_prices": _market_prices_to_dict(
            market_snapshot_from_chainlink(legacy),
        ),
    }


def _atomic(value: object) -> int:
    if not isinstance(value, str) or not value.isdecimal():
        raise ValueError("Public cache amount is invalid")
    result = int(value)
    if result < 0 or result >= 2**256:
        raise ValueError("Public cache amount is invalid")
    return result


def _positive_int(value: object, *, allow_zero: bool = False) -> int:
    if type(value) is not int or value < (0 if allow_zero else 1):
        raise ValueError("Public cache number is invalid")
    return value


def _parse_time(value: str) -> None:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Public cache timestamp is invalid")


def _utc_now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
