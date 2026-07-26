"""Strict public-only cache for the last successful Wallet portfolio read."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Mapping

from .prices import AssetPrice, PriceSnapshot, PriceStatus
from .public_data import (
    NETWORK_BY_ID, AssetBalance, NetworkSnapshot, PublicDataStatus,
)
from .storage import StorageError, WalletPaths, atomic_write_json, read_json


CACHE_SCHEMA_VERSION = 1
MAX_CACHED_PROFILES = 50


@dataclass(frozen=True, slots=True)
class CachedPublicBundle:
    profile_id: str
    address: str
    networks: tuple[NetworkSnapshot, ...]
    prices: PriceSnapshot
    saved_at: str


class PublicCacheStore:
    """Persists public balances and prices without granting data authority."""

    def __init__(self, paths: WalletPaths) -> None:
        self.path = paths.public_cache

    def load(self, profile_id: str, address: str) -> CachedPublicBundle | None:
        try:
            profiles = self._load_profiles()
            value = next(
                item for item in profiles
                if item.get("profile_id") == profile_id
                and item.get("address") == address
            )
            return _bundle_from_dict(value)
        except (StopIteration, StorageError, TypeError, ValueError):
            return None

    def save(
        self,
        profile_id: str,
        address: str,
        networks: Mapping[str, NetworkSnapshot],
        prices: PriceSnapshot,
    ) -> None:
        successful = tuple(
            networks[network_id]
            for network_id in NETWORK_BY_ID
            if network_id in networks
            and networks[network_id].status is PublicDataStatus.LIVE
        )
        if not successful or prices.status is not PriceStatus.LIVE:
            return
        saved_at = _utc_now()
        candidate = _bundle_to_dict(
            CachedPublicBundle(profile_id, address, successful, prices, saved_at)
        )
        try:
            profiles = self._load_profiles()
        except (StorageError, TypeError, ValueError):
            profiles = []
        retained = [
            item for item in profiles
            if item.get("profile_id") != profile_id
        ][-(MAX_CACHED_PROFILES - 1):]
        retained.append(candidate)
        atomic_write_json(
            self.path,
            {"schema_version": CACHE_SCHEMA_VERSION, "profiles": retained},
        )

    def _load_profiles(self) -> list[dict[str, object]]:
        if not self.path.exists():
            return []
        value = read_json(self.path)
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "profiles"}
            or value.get("schema_version") != CACHE_SCHEMA_VERSION
            or not isinstance(value.get("profiles"), list)
            or len(value["profiles"]) > MAX_CACHED_PROFILES
            or any(not isinstance(item, dict) for item in value["profiles"])
        ):
            raise ValueError("Public cache is invalid")
        return list(value["profiles"])


def _bundle_to_dict(bundle: CachedPublicBundle) -> dict[str, object]:
    return {
        "profile_id": bundle.profile_id,
        "address": bundle.address,
        "saved_at": bundle.saved_at,
        "networks": [
            {
                "network_id": item.network_id,
                "chain_id": item.chain_id,
                "block_number": item.block_number,
                "updated_at": item.updated_at,
                "eth_atomic": str(item.eth.atomic_units),
                "usdc_atomic": str(item.usdc.atomic_units),
            }
            for item in bundle.networks
            if item.eth is not None and item.usdc is not None
        ],
        "prices": {
            "chain_id": bundle.prices.chain_id,
            "observed_at": bundle.prices.observed_at,
            "assets": [
                {
                    "asset_id": item.asset_id,
                    "symbol": item.symbol,
                    "answer": item.answer,
                    "decimals": item.decimals,
                    "updated_at": item.updated_at,
                }
                for item in bundle.prices.prices
            ],
        },
    }


def _bundle_from_dict(value: object) -> CachedPublicBundle:
    if not isinstance(value, dict) or set(value) != {
        "profile_id", "address", "saved_at", "networks", "prices",
    }:
        raise ValueError("Public cache profile is invalid")
    profile_id, address, saved_at = (
        value["profile_id"], value["address"], value["saved_at"],
    )
    if not all(isinstance(item, str) and item for item in (profile_id, address, saved_at)):
        raise ValueError("Public cache identity is invalid")
    _parse_time(saved_at)
    raw_networks = value["networks"]
    if not isinstance(raw_networks, list) or not 1 <= len(raw_networks) <= len(NETWORK_BY_ID):
        raise ValueError("Public cache networks are invalid")
    networks = tuple(_network_from_dict(item) for item in raw_networks)
    if len({item.network_id for item in networks}) != len(networks):
        raise ValueError("Public cache networks are duplicated")
    prices = _prices_from_dict(value["prices"])
    return CachedPublicBundle(profile_id, address, networks, prices, saved_at)


def _network_from_dict(value: object) -> NetworkSnapshot:
    if not isinstance(value, dict) or set(value) != {
        "network_id", "chain_id", "block_number", "updated_at",
        "eth_atomic", "usdc_atomic",
    }:
        raise ValueError("Public cache network is invalid")
    network_id = value["network_id"]
    if network_id not in NETWORK_BY_ID:
        raise ValueError("Public cache network is unsupported")
    spec = NETWORK_BY_ID[network_id]
    if value["chain_id"] != spec.chain_id:
        raise ValueError("Public cache chain is invalid")
    block = _positive_int(value["block_number"])
    updated_at = value["updated_at"]
    if not isinstance(updated_at, str):
        raise ValueError("Public cache timestamp is invalid")
    _parse_time(updated_at)
    eth = _atomic(value["eth_atomic"])
    usdc = _atomic(value["usdc_atomic"])
    return NetworkSnapshot(
        network_id, spec.label, spec.chain_id, PublicDataStatus.LIVE, block,
        AssetBalance("ETH", eth, 18), AssetBalance("USDC", usdc, 6), updated_at,
    )


def _prices_from_dict(value: object) -> PriceSnapshot:
    if not isinstance(value, dict) or set(value) != {
        "chain_id", "observed_at", "assets",
    } or value.get("chain_id") != 8453:
        raise ValueError("Public cache prices are invalid")
    observed_at = _positive_int(value["observed_at"])
    raw_assets = value["assets"]
    if not isinstance(raw_assets, list) or len(raw_assets) != 2:
        raise ValueError("Public cache price assets are invalid")
    assets = tuple(_price_from_dict(item) for item in raw_assets)
    if {item.asset_id for item in assets} != {"eth", "usdc"}:
        raise ValueError("Public cache price assets are invalid")
    return PriceSnapshot(8453, PriceStatus.LIVE, assets, observed_at)


def _price_from_dict(value: object) -> AssetPrice:
    if not isinstance(value, dict) or set(value) != {
        "asset_id", "symbol", "answer", "decimals", "updated_at",
    }:
        raise ValueError("Public cache price is invalid")
    asset_id = value["asset_id"]
    expected_symbol = {"eth": "ETH", "usdc": "USDC"}.get(asset_id)
    if value["symbol"] != expected_symbol:
        raise ValueError("Public cache price identity is invalid")
    answer = _positive_int(value["answer"])
    decimals = _positive_int(value["decimals"], allow_zero=True)
    updated_at = _positive_int(value["updated_at"])
    return AssetPrice(
        str(asset_id), str(expected_symbol), PriceStatus.LIVE,
        answer, decimals, updated_at,
    )


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
