"""Strict, non-authoritative identities for Lending read adapters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from web3 import Web3


BASE_CHAIN_ID = 8453
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

ROOT_FIELDS = frozenset({
    "asset", "morpho_discovery", "network", "profile_version", "protocols",
    "schema_version", "sources", "verified_at",
})
NETWORK_FIELDS = frozenset({"chain_id", "network_id"})
ASSET_FIELDS = frozenset({"address", "asset_id", "decimals"})
PROTOCOL_FIELDS = frozenset({
    "contracts", "market_id", "position_source", "protocol_id", "rate_source",
})
MORPHO_FIELDS = frozenset({"collections", "endpoint", "filters", "selected_vault"})
MORPHO_FILTER_FIELDS = frozenset({
    "asset_address", "asset_decimals", "chain_id", "listed",
})
SOURCE_FIELDS = frozenset({"revision", "source_id", "url"})

AAVE_CONTRACTS = {
    "a_token": "0x4e65fE4DbA92790696d040ac24Aa414708F5c0AB",
    "pool": "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5",
    "pool_addresses_provider": "0xe20fCBdBfFC4Dd138cE8b2E6FBb6CB49777ad64D",
    "protocol_data_provider": "0x0F43731EB8d45A581f4a36DD74F5f358bc90C73A",
}
COMPOUND_CONTRACTS = {
    "comet": "0xb125E6687d4313864e53df431d5425969c15Eb2F",
    "rewards": "0x123964802e6ABabBE1Bc9547D72Ef1B69B00A6b1",
}
EXPECTED_SOURCES = (
    (
        "circle-usdc",
        "https://developers.circle.com/stablecoins/usdc-contract-addresses",
        "2026-07-25",
    ),
    (
        "aave-address-book",
        "https://raw.githubusercontent.com/bgd-labs/aave-address-book/"
        "4ae19b95f84b077c28633ca1d0f9a6750a3ea1d4/src/AaveV3Base.sol",
        "4ae19b95f84b077c28633ca1d0f9a6750a3ea1d4",
    ),
    (
        "compound-comet-base-usdc",
        "https://raw.githubusercontent.com/compound-finance/comet/"
        "f766f51583c23acc33b2a7824654ef2029a96804/deployments/base/usdc/roots.json",
        "f766f51583c23acc33b2a7824654ef2029a96804",
    ),
    (
        "morpho-vaults-api",
        "https://docs.morpho.org/developers/api/morpho-vaults/",
        "2026-07-25",
    ),
)


class ReadProfilesValidationError(ValueError):
    def __init__(self, message: str, *, incompatible: bool = False) -> None:
        super().__init__(message)
        self.incompatible = incompatible


def _object(value: Any, fields: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise ReadProfilesValidationError(f"{name} fields are invalid")
    return value


def _exact_type(value: Any, expected: type, name: str) -> None:
    if type(value) is not expected:
        raise ReadProfilesValidationError(f"{name} has an invalid type")


def _address(value: Any, name: str) -> str:
    _exact_type(value, str, name)
    if value == ZERO_ADDRESS or not Web3.is_checksum_address(value):
        raise ReadProfilesValidationError(f"{name} is not a checksum address")
    return value


@dataclass(frozen=True)
class ProtocolReadProfile:
    protocol_id: str
    market_id: str
    contracts: tuple[tuple[str, str], ...]
    rate_source: str
    position_source: str

    @classmethod
    def from_dict(cls, value: Any) -> "ProtocolReadProfile":
        item = _object(value, PROTOCOL_FIELDS, "protocol")
        for field in ("protocol_id", "market_id", "rate_source", "position_source"):
            _exact_type(item[field], str, field)
        protocol_id = item["protocol_id"]
        expected = {
            "aave-v3": (
                AAVE_CONTRACTS,
                "pool.getReserveData.currentLiquidityRate_ray_apr",
                "a_token.balanceOf",
            ),
            "compound-v3": (
                COMPOUND_CONTRACTS,
                "comet.getSupplyRate(comet.getUtilization())_per_second_wad_apr",
                "comet.balanceOf",
            ),
        }.get(protocol_id)
        if expected is None:
            raise ReadProfilesValidationError("protocol_id is unsupported")
        contracts = item["contracts"]
        if not isinstance(contracts, dict) or set(contracts) != set(expected[0]):
            raise ReadProfilesValidationError("protocol contracts are invalid")
        normalized = tuple((key, _address(contracts[key], key)) for key in sorted(contracts))
        if dict(normalized) != expected[0]:
            raise ReadProfilesValidationError("protocol contract identity changed")
        if item["market_id"] != "base-usdc" or tuple(item[name] for name in (
            "rate_source", "position_source"
        )) != expected[1:]:
            raise ReadProfilesValidationError("protocol read semantics changed")
        return cls(
            protocol_id,
            item["market_id"],
            normalized,
            item["rate_source"],
            item["position_source"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "contracts": dict(self.contracts),
            "market_id": self.market_id,
            "position_source": self.position_source,
            "protocol_id": self.protocol_id,
            "rate_source": self.rate_source,
        }


@dataclass(frozen=True)
class LendingReadProfiles:
    verified_at: str
    protocols: tuple[ProtocolReadProfile, ...]
    sources: tuple[tuple[str, str, str], ...]

    @classmethod
    def from_dict(cls, value: Any) -> "LendingReadProfiles":
        root = _object(value, ROOT_FIELDS, "read profiles")
        if root["schema_version"] != "1" or root["profile_version"] != "1":
            raise ReadProfilesValidationError(
                "read profile version is incompatible", incompatible=True
            )
        network = _object(root["network"], NETWORK_FIELDS, "network")
        if network != {"chain_id": BASE_CHAIN_ID, "network_id": "base"}:
            raise ReadProfilesValidationError("network identity changed")
        asset = _object(root["asset"], ASSET_FIELDS, "asset")
        _address(asset["address"], "asset address")
        if asset != {"address": BASE_USDC, "asset_id": "usdc", "decimals": 6}:
            raise ReadProfilesValidationError("asset identity changed")
        raw_protocols = root["protocols"]
        if not isinstance(raw_protocols, list):
            raise ReadProfilesValidationError("protocols must be an array")
        protocols = tuple(ProtocolReadProfile.from_dict(item) for item in raw_protocols)
        if tuple(item.protocol_id for item in protocols) != ("aave-v3", "compound-v3"):
            raise ReadProfilesValidationError("protocol ordering is invalid")
        cls._validate_morpho(root["morpho_discovery"])
        sources = cls._validate_sources(root["sources"])
        _exact_type(root["verified_at"], str, "verified_at")
        if root["verified_at"] != "2026-07-25":
            raise ReadProfilesValidationError("verification date changed")
        return cls(root["verified_at"], protocols, sources)

    @staticmethod
    def _validate_morpho(value: Any) -> None:
        morpho = _object(value, MORPHO_FIELDS, "morpho discovery")
        if morpho["endpoint"] != "https://api.morpho.org/graphql":
            raise ReadProfilesValidationError("Morpho endpoint changed")
        if morpho["collections"] != ["vaults", "vaultV2s"]:
            raise ReadProfilesValidationError("Morpho collections changed")
        if morpho["selected_vault"] is not None:
            raise ReadProfilesValidationError("Morpho vault selection is deferred")
        filters = _object(morpho["filters"], MORPHO_FILTER_FIELDS, "Morpho filters")
        expected = {
            "asset_address": BASE_USDC,
            "asset_decimals": 6,
            "chain_id": BASE_CHAIN_ID,
            "listed": True,
        }
        _address(filters["asset_address"], "Morpho asset address")
        if filters != expected:
            raise ReadProfilesValidationError("Morpho filters changed")

    @staticmethod
    def _validate_sources(value: Any) -> tuple[tuple[str, str, str], ...]:
        if not isinstance(value, list) or len(value) != 4:
            raise ReadProfilesValidationError("sources are invalid")
        sources: list[tuple[str, str, str]] = []
        for raw in value:
            source = _object(raw, SOURCE_FIELDS, "source")
            for field in SOURCE_FIELDS:
                _exact_type(source[field], str, f"source {field}")
            sources.append((source["source_id"], source["url"], source["revision"]))
        if tuple(sources) != EXPECTED_SOURCES:
            raise ReadProfilesValidationError("source identities or ordering changed")
        return tuple(sources)

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset": {"address": BASE_USDC, "asset_id": "usdc", "decimals": 6},
            "morpho_discovery": {
                "collections": ["vaults", "vaultV2s"],
                "endpoint": "https://api.morpho.org/graphql",
                "filters": {
                    "asset_address": BASE_USDC, "asset_decimals": 6,
                    "chain_id": BASE_CHAIN_ID, "listed": True,
                },
                "selected_vault": None,
            },
            "network": {"chain_id": BASE_CHAIN_ID, "network_id": "base"},
            "profile_version": "1",
            "protocols": [item.to_dict() for item in self.protocols],
            "schema_version": "1",
            "sources": [
                {"source_id": item[0], "url": item[1], "revision": item[2]}
                for item in self.sources
            ],
            "verified_at": self.verified_at,
        }
