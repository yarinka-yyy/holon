"""Strict, project-owned EVM network and asset registry."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Mapping


ID_RE = re.compile(r"^[a-z][a-z0-9-]{0,31}$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
CAPABILITIES = frozenset({"balance", "receive", "transfer", "price", "lending"})


class RegistryError(ValueError):
    """Pinned registry data is missing, incompatible, or unsafe."""


@dataclass(frozen=True, slots=True)
class NetworkRecord:
    network_id: str
    display_name: str
    chain_id: int
    native_asset_id: str
    rpc_env: str
    default_rpc: str
    explorer_url: str
    icon_path: str
    capabilities: frozenset[str]


@dataclass(frozen=True, slots=True)
class AssetRecord:
    asset_id: str
    display_symbol: str
    onchain_symbols: tuple[str, ...]
    display_name: str
    decimals: int
    icon_path: str
    price_asset_id: str | None


@dataclass(frozen=True, slots=True)
class DeploymentRecord:
    network_id: str
    asset_id: str
    kind: str
    contract_address: str | None
    capabilities: frozenset[str]
    source_url: str
    source_revision: str

    @property
    def deployment_id(self) -> str:
        return f"{self.network_id}:{self.asset_id}"


@dataclass(frozen=True, slots=True)
class NetworkAssetRegistry:
    revision: str
    networks: tuple[NetworkRecord, ...]
    assets: tuple[AssetRecord, ...]
    deployments: tuple[DeploymentRecord, ...]
    network_by_id: Mapping[str, NetworkRecord]
    asset_by_id: Mapping[str, AssetRecord]
    deployments_by_network: Mapping[str, tuple[DeploymentRecord, ...]]


def _exact(value: object, fields: set[str], name: str) -> dict:
    if not isinstance(value, dict) or set(value) != fields:
        raise RegistryError(f"Invalid {name} record")
    return value


def _https(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("https://"):
        raise RegistryError("Registry provenance must use HTTPS")
    return value


def _caps(value: object) -> frozenset[str]:
    if (
        not isinstance(value, list)
        or any(not isinstance(item, str) for item in value)
        or len(value) != len(set(value))
    ):
        raise RegistryError("Invalid registry capabilities")
    result = frozenset(value)
    if not result <= CAPABILITIES:
        raise RegistryError("Unknown registry capability")
    return result


def load_registry(path: Path | None = None) -> NetworkAssetRegistry:
    source = path or Path(__file__).with_name("network-assets.json")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RegistryError("Network and asset registry is unavailable") from exc
    root = _exact(raw, {"schema_version", "revision", "networks", "assets", "deployments"}, "registry")
    if root["schema_version"] != 1 or not isinstance(root["revision"], str):
        raise RegistryError("Unsupported registry schema")
    networks: list[NetworkRecord] = []
    network_ids: set[str] = set()
    chain_ids: set[int] = set()
    for item in root["networks"]:
        value = _exact(item, {"network_id", "display_name", "chain_id", "native_asset_id", "rpc_env", "default_rpc", "explorer_url", "icon", "capabilities"}, "network")
        icon = _exact(value["icon"], {"path", "source_url", "source_revision", "source_sha256"}, "icon")
        network_id, chain_id = value["network_id"], value["chain_id"]
        if not isinstance(network_id, str) or not ID_RE.fullmatch(network_id) or network_id in network_ids or type(chain_id) is not int or chain_id <= 0 or chain_id in chain_ids:
            raise RegistryError("Duplicate or invalid network identity")
        if (
            not isinstance(icon["path"], str)
            or not icon["path"].startswith("qml/assets/")
            or not isinstance(icon["source_revision"], str)
            or not icon["source_revision"]
            or not SHA256_RE.fullmatch(str(icon["source_sha256"]))
        ):
            raise RegistryError("Invalid network icon provenance")
        _https(icon["source_url"]); _https(value["default_rpc"]); _https(value["explorer_url"])
        if not all(isinstance(value[name], str) and value[name] for name in ("display_name", "native_asset_id", "rpc_env")):
            raise RegistryError("Invalid network metadata")
        networks.append(NetworkRecord(network_id, value["display_name"], chain_id, value["native_asset_id"], value["rpc_env"], value["default_rpc"], value["explorer_url"], icon["path"], _caps(value["capabilities"])))
        network_ids.add(network_id); chain_ids.add(chain_id)
    assets: list[AssetRecord] = []
    asset_ids: set[str] = set()
    for item in root["assets"]:
        value = _exact(item, {"asset_id", "display_symbol", "onchain_symbols", "display_name", "decimals", "icon", "price_asset_id"}, "asset")
        icon = _exact(value["icon"], {"path", "source_url", "source_revision", "source_sha256"}, "icon")
        asset_id, decimals = value["asset_id"], value["decimals"]
        symbols = value["onchain_symbols"]
        if not isinstance(asset_id, str) or not ID_RE.fullmatch(asset_id) or asset_id in asset_ids or type(decimals) is not int or not 0 <= decimals <= 255 or not isinstance(symbols, list) or not symbols or any(not isinstance(symbol, str) or not symbol for symbol in symbols):
            raise RegistryError("Duplicate or invalid asset identity")
        if (
            not isinstance(icon["path"], str)
            or not icon["path"].startswith("qml/assets/")
            or not isinstance(icon["source_revision"], str)
            or not icon["source_revision"]
            or not SHA256_RE.fullmatch(str(icon["source_sha256"]))
        ):
            raise RegistryError("Invalid asset icon provenance")
        _https(icon["source_url"])
        if (
            not isinstance(value["display_symbol"], str)
            or not value["display_symbol"]
            or not isinstance(value["display_name"], str)
            or not value["display_name"]
            or value["price_asset_id"] is not None
            and not isinstance(value["price_asset_id"], str)
        ):
            raise RegistryError("Invalid asset metadata")
        assets.append(AssetRecord(asset_id, value["display_symbol"], tuple(symbols), value["display_name"], decimals, icon["path"], value["price_asset_id"]))
        asset_ids.add(asset_id)
    deployments: list[DeploymentRecord] = []
    deployment_ids: set[tuple[str, str]] = set()
    contracts: set[tuple[str, str]] = set()
    by_network: dict[str, list[DeploymentRecord]] = {item.network_id: [] for item in networks}
    for item in root["deployments"]:
        value = _exact(item, {"network_id", "asset_id", "kind", "contract_address", "capabilities", "source_url", "source_revision", "reviewed"}, "deployment")
        key = (value["network_id"], value["asset_id"]); address = value["contract_address"]
        if key in deployment_ids or key[0] not in network_ids or key[1] not in asset_ids or value["kind"] not in {"native", "erc20"} or value["reviewed"] is not True:
            raise RegistryError("Invalid asset deployment")
        if value["kind"] == "native" and address is not None or value["kind"] == "erc20" and (not isinstance(address, str) or not ADDRESS_RE.fullmatch(address)):
            raise RegistryError("Invalid deployment address")
        if address is not None and (key[0], address.lower()) in contracts:
            raise RegistryError("Duplicate deployment contract")
        if not isinstance(value["source_revision"], str) or not value["source_revision"]:
            raise RegistryError("Missing deployment provenance")
        record = DeploymentRecord(key[0], key[1], value["kind"], address, _caps(value["capabilities"]), _https(value["source_url"]), value["source_revision"])
        deployments.append(record); by_network[key[0]].append(record); deployment_ids.add(key)
        if address is not None: contracts.add((key[0], address.lower()))
    for network in networks:
        native = [item for item in by_network[network.network_id] if item.kind == "native"]
        if len(native) != 1 or native[0].asset_id != network.native_asset_id or any(not item.capabilities <= network.capabilities for item in by_network[network.network_id]):
            raise RegistryError("Invalid network capability binding")
    asset_map = MappingProxyType({item.asset_id: item for item in assets})
    if any(item.price_asset_id is not None and item.price_asset_id not in asset_map for item in assets):
        raise RegistryError("Unknown price asset identity")
    return NetworkAssetRegistry(root["revision"], tuple(networks), tuple(assets), tuple(deployments), MappingProxyType({item.network_id: item for item in networks}), asset_map, MappingProxyType({key: tuple(value) for key, value in by_network.items()}))
