"""Bounded, registry-owned read-only EVM public-data service."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from enum import Enum
from typing import Callable, Mapping, Protocol

import requests
from requests import exceptions as request_errors
from web3 import Web3

from holon_contracts.registry import DeploymentRecord, load_registry


_REGISTRY = load_registry()
ETHEREUM_USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
USDC_ABI = (
    {
        "inputs": ({"internalType": "address", "name": "account", "type": "address"},),
        "name": "balanceOf", "outputs": ({"internalType": "uint256", "name": "", "type": "uint256"},),
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": (
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"},
        ),
        "name": "allowance", "outputs": ({"internalType": "uint256", "name": "", "type": "uint256"},),
        "stateMutability": "view", "type": "function",
    },
    {
        "inputs": (), "name": "decimals",
        "outputs": ({"internalType": "uint8", "name": "", "type": "uint8"},),
        "stateMutability": "view", "type": "function",
    },
)


class PublicDataStatus(str, Enum):
    LIVE = "LIVE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"
    SIMULATED = "SIMULATED"


@dataclass(frozen=True, slots=True)
class AssetSpec:
    asset_id: str
    symbol: str
    decimals: int
    onchain_symbols: tuple[str, ...]
    contract: str | None
    icon_path: str
    market_price_id: str | None


@dataclass(frozen=True, slots=True)
class NetworkSpec:
    network_id: str
    label: str
    chain_id: int
    endpoint_env: str
    default_endpoint: str
    native_asset_id: str
    assets: tuple[AssetSpec, ...]

    @property
    def usdc_contract(self) -> str:
        item = next(asset for asset in self.assets if asset.asset_id == "usdc")
        if item.contract is None:
            raise ValueError("USDC deployment is invalid")
        return item.contract


def _asset_spec(deployment: DeploymentRecord) -> AssetSpec:
    asset = _REGISTRY.asset_by_id[deployment.asset_id]
    return AssetSpec(
        asset.asset_id, asset.display_symbol, asset.decimals,
        asset.onchain_symbols, deployment.contract_address, asset.icon_path,
        asset.market_price_id,
    )


NETWORKS = tuple(
    NetworkSpec(
        item.network_id, item.display_name, item.chain_id, item.rpc_env,
        item.default_rpc, item.native_asset_id,
        tuple(_asset_spec(value) for value in _REGISTRY.deployments_by_network[item.network_id]),
    )
    for item in _REGISTRY.networks
)
NETWORK_BY_ID = {network.network_id: network for network in NETWORKS}


@dataclass(frozen=True, slots=True)
class AssetBalance:
    symbol: str
    atomic_units: int
    decimals: int
    asset_id: str = field(default="", compare=False)
    updated_at: str | None = field(default=None, compare=False)

    @property
    def display_value(self) -> str:
        return format_units(self.atomic_units, self.decimals, self.symbol)


@dataclass(frozen=True, slots=True)
class AssetReadError:
    asset_id: str
    error_code: str


@dataclass(frozen=True, slots=True)
class NetworkSnapshot:
    network_id: str
    label: str
    chain_id: int
    status: PublicDataStatus
    block_number: int | None
    eth: AssetBalance | None
    usdc: AssetBalance | None
    updated_at: str | None
    error_code: str | None = None
    assets: tuple[AssetBalance, ...] = ()
    asset_errors: tuple[AssetReadError, ...] = ()

    def __post_init__(self) -> None:
        assets = self.assets
        if not assets:
            legacy: list[AssetBalance] = []
            if self.eth is not None:
                legacy.append(_with_id(self.eth, "eth"))
            if self.usdc is not None:
                legacy.append(_with_id(self.usdc, "usdc"))
            assets = tuple(legacy)
        assets = tuple(
            item if item.updated_at is not None or self.updated_at is None
            else replace(item, updated_at=self.updated_at)
            for item in assets
        )
        object.__setattr__(self, "assets", assets)
        object.__setattr__(self, "eth", next((a for a in assets if a.asset_id == "eth"), None))
        object.__setattr__(self, "usdc", next((a for a in assets if a.asset_id == "usdc"), None))

    @property
    def assets_by_id(self) -> Mapping[str, AssetBalance]:
        return {item.asset_id: item for item in self.assets}

    @property
    def errors_by_id(self) -> Mapping[str, str]:
        return {item.asset_id: item.error_code for item in self.asset_errors}

    @classmethod
    def unavailable(cls, spec: NetworkSpec, code: str) -> NetworkSnapshot:
        return cls(
            spec.network_id, spec.label, spec.chain_id, PublicDataStatus.UNAVAILABLE,
            None, None, None, None, code, (),
            tuple(AssetReadError(item.asset_id, code) for item in spec.assets),
        )


@dataclass(frozen=True, slots=True)
class PortfolioSnapshot:
    profile_id: str
    address: str
    networks: tuple[NetworkSnapshot, ...]


class PublicRpc(Protocol):
    def chain_id(self) -> int: ...
    def block_number(self) -> int: ...
    def native_balance(self, address: str) -> int: ...
    def token_decimals(self, contract: str) -> int: ...
    def token_balance(self, contract: str, address: str) -> int: ...


class Web3PublicRpc:
    """Fixed read methods plus one bounded JSON-RPC batch at a pinned block."""

    def __init__(self, endpoint: str, timeout_seconds: float = 5.0) -> None:
        self._endpoint = endpoint
        self._timeout = timeout_seconds
        provider = Web3.HTTPProvider(
            endpoint, request_kwargs={"timeout": timeout_seconds},
            exception_retry_configuration=None,
        )
        self._web3 = Web3(provider)

    def chain_id(self) -> int:
        return int(self._web3.eth.chain_id)

    def block_number(self) -> int:
        return int(self._web3.eth.block_number)

    def native_balance(self, address: str) -> int:
        return int(self._web3.eth.get_balance(Web3.to_checksum_address(address)))

    def token_decimals(self, contract: str) -> int:
        return int(self._call_uint(contract, "0x313ce567"))

    def token_balance(self, contract: str, address: str) -> int:
        return int(self._call_uint(contract, _balance_of_data(address)))

    def asset_balances(
        self, spec: NetworkSpec, address: str, block_number: int,
    ) -> tuple[tuple[AssetBalance, ...], tuple[AssetReadError, ...]]:
        block = hex(block_number)
        queries: list[tuple[str, str, list[object]]] = []
        for asset in spec.assets:
            if asset.contract is None:
                queries.append((f"{asset.asset_id}:balance", "eth_getBalance", [address, block]))
            else:
                base = {"to": asset.contract}
                queries.extend((
                    (f"{asset.asset_id}:balance", "eth_call", [{**base, "data": _balance_of_data(address)}, block]),
                    (f"{asset.asset_id}:decimals", "eth_call", [{**base, "data": "0x313ce567"}, block]),
                    (f"{asset.asset_id}:symbol", "eth_call", [{**base, "data": "0x95d89b41"}, block]),
                ))
        results = self._batch_with_fallback(queries)
        balances: list[AssetBalance] = []
        errors: list[AssetReadError] = []
        for asset in spec.assets:
            try:
                amount = _rpc_uint(results[f"{asset.asset_id}:balance"])
                if asset.contract is not None:
                    decimals = _rpc_uint(results[f"{asset.asset_id}:decimals"])
                    symbol = _rpc_symbol(results[f"{asset.asset_id}:symbol"])
                    if decimals != asset.decimals or symbol not in asset.onchain_symbols:
                        raise _MetadataMismatch
                balances.append(AssetBalance(asset.symbol, amount, asset.decimals, asset.asset_id))
            except _MetadataMismatch:
                errors.append(AssetReadError(asset.asset_id, "TOKEN_METADATA_INVALID"))
            except (KeyError, TypeError, ValueError):
                errors.append(AssetReadError(asset.asset_id, "TOKEN_DATA_UNAVAILABLE"))
        return tuple(balances), tuple(errors)

    def _batch_with_fallback(
        self, queries: list[tuple[str, str, list[object]]],
    ) -> dict[str, object]:
        payload = [
            {"jsonrpc": "2.0", "id": key, "method": method, "params": params}
            for key, method, params in queries
        ]
        response = requests.post(self._endpoint, json=payload, timeout=self._timeout)
        response.raise_for_status()
        body = response.json()
        retryable = _retryable_rpc_error(body)
        if retryable is not None:
            raise retryable
        mapped = _map_rpc_results(body) if isinstance(body, list) else {}
        for key, method, params in queries:
            if key not in mapped:
                result = self._post_one(key, method, params)
                if result is not None:
                    mapped[key] = result
        return mapped

    def _post_one(self, key: str, method: str, params: list[object]) -> object | None:
        response = requests.post(
            self._endpoint,
            json={"jsonrpc": "2.0", "id": key, "method": method, "params": params},
            timeout=self._timeout,
        )
        response.raise_for_status()
        body = response.json()
        retryable = _retryable_rpc_error(body)
        if retryable is not None:
            raise retryable
        if not isinstance(body, dict) or body.get("id") != key or "error" in body:
            return None
        return body.get("result")

    def _call_uint(self, contract: str, data: str) -> int:
        result = self._web3.eth.call({"to": Web3.to_checksum_address(contract), "data": data})
        return int.from_bytes(result, "big")


RpcFactory = Callable[[str, str], PublicRpc]


class PublicDataService:
    """Reads only registry-allowlisted balances; it never touches signing data."""

    def __init__(
        self, rpc_factory: RpcFactory | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._rpc_factory = rpc_factory or self._default_rpc_factory
        self._environ = os.environ if environ is None else environ

    def refresh(
        self, profile_id: str, address: str,
        network_ids: tuple[str, ...] | None = None,
    ) -> PortfolioSnapshot:
        selected = network_ids or tuple(item.network_id for item in NETWORKS)
        if not selected or any(item not in NETWORK_BY_ID for item in selected):
            raise ValueError("Unsupported public-data network")
        pool = ThreadPoolExecutor(max_workers=min(4, len(selected)), thread_name_prefix="holon-evm-read")
        futures = {
            item: pool.submit(self._read_network, NETWORK_BY_ID[item], address)
            for item in selected
        }
        done, _pending = wait(tuple(futures.values()), timeout=20.0)
        snapshots = tuple(
            futures[item].result()
            if futures[item] in done
            else NetworkSnapshot.unavailable(NETWORK_BY_ID[item], "RPC_TIMEOUT")
            for item in selected
        )
        pool.shutdown(wait=False, cancel_futures=True)
        return PortfolioSnapshot(profile_id, address, snapshots)

    def _read_network(self, spec: NetworkSpec, address: str) -> NetworkSnapshot:
        endpoint = self._environ.get(spec.endpoint_env, spec.default_endpoint).strip()
        if not endpoint:
            return NetworkSnapshot.unavailable(spec, "RPC_UNAVAILABLE")
        attempts = 0
        while True:
            try:
                rpc = self._rpc_factory(spec.network_id, endpoint)
                if rpc.chain_id() != spec.chain_id:
                    return NetworkSnapshot.unavailable(spec, "WRONG_CHAIN")
                block = _non_negative(rpc.block_number())
                if hasattr(rpc, "asset_balances"):
                    balances, errors = rpc.asset_balances(spec, address, block)  # type: ignore[attr-defined]
                else:
                    balances, errors = _legacy_balances(rpc, spec, address)
                if not balances:
                    return NetworkSnapshot.unavailable(spec, "RPC_UNAVAILABLE")
                status = PublicDataStatus.PARTIAL if errors else PublicDataStatus.LIVE
                by_id = {item.asset_id: item for item in balances}
                return NetworkSnapshot(
                    spec.network_id, spec.label, spec.chain_id, status, block,
                    by_id.get("eth"), by_id.get("usdc"), _utc_now(),
                    "ASSET_DATA_UNAVAILABLE" if errors else None, balances, errors,
                )
            except _RETRYABLE_ERRORS as exc:
                if attempts >= 1:
                    return NetworkSnapshot.unavailable(spec, _rpc_error_code(exc))
                attempts += 1
            except _MetadataMismatch:
                return NetworkSnapshot.unavailable(spec, "TOKEN_METADATA_INVALID")
            except (TypeError, ValueError, ArithmeticError):
                return NetworkSnapshot.unavailable(spec, "DATA_INVALID")
            except Exception:
                return NetworkSnapshot.unavailable(spec, "RPC_UNAVAILABLE")

    @staticmethod
    def _default_rpc_factory(_network_id: str, endpoint: str) -> PublicRpc:
        return Web3PublicRpc(endpoint)


class _MetadataMismatch(Exception):
    pass


class _RetryableRpcError(Exception):
    def __init__(self, error_code: str) -> None:
        super().__init__(error_code)
        self.error_code = error_code


def _legacy_balances(
    rpc: PublicRpc, spec: NetworkSpec, address: str,
) -> tuple[tuple[AssetBalance, ...], tuple[AssetReadError, ...]]:
    native_spec = next(item for item in spec.assets if item.asset_id == spec.native_asset_id)
    balances = [AssetBalance(
        native_spec.symbol, _non_negative(rpc.native_balance(address)),
        native_spec.decimals, native_spec.asset_id,
    )]
    usdc_spec = next(
        (item for item in spec.assets if item.asset_id == "usdc"), None,
    )
    if usdc_spec is not None:
        if (
            usdc_spec.contract is None
            or rpc.token_decimals(usdc_spec.contract) != usdc_spec.decimals
        ):
            raise _MetadataMismatch
        balances.append(AssetBalance(
            usdc_spec.symbol,
            _non_negative(rpc.token_balance(usdc_spec.contract, address)),
            usdc_spec.decimals,
            usdc_spec.asset_id,
        ))
    return tuple(balances), ()


def _with_id(balance: AssetBalance, asset_id: str) -> AssetBalance:
    return balance if balance.asset_id else AssetBalance(
        balance.symbol, balance.atomic_units, balance.decimals, asset_id,
        balance.updated_at,
    )


def _balance_of_data(address: str) -> str:
    checked = Web3.to_checksum_address(address)
    return "0x70a08231" + checked[2:].lower().rjust(64, "0")


def _map_rpc_results(value: list[object]) -> dict[str, object]:
    mapped: dict[str, object] = {}
    for item in value:
        if (
            isinstance(item, dict) and isinstance(item.get("id"), str)
            and "result" in item and "error" not in item
        ):
            mapped[item["id"]] = item["result"]
    return mapped


def _rpc_uint(value: object) -> int:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("Invalid RPC number")
    result = int(value, 16)
    if result < 0 or result >= 2**256:
        raise ValueError("Invalid RPC number")
    return result


def _rpc_symbol(value: object) -> str:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError("Invalid RPC symbol")
    raw = bytes.fromhex(value[2:])
    if len(raw) >= 64 and int.from_bytes(raw[:32], "big") == 32:
        length = int.from_bytes(raw[32:64], "big")
        raw = raw[64:64 + length]
    else:
        raw = raw[:32].rstrip(b"\0")
    return raw.decode("utf-8")


def _rpc_error_code(exc: BaseException) -> str:
    if isinstance(exc, _RetryableRpcError):
        return exc.error_code
    if isinstance(exc, (request_errors.Timeout, TimeoutError)):
        return "RPC_TIMEOUT"
    if isinstance(exc, request_errors.HTTPError) and exc.response is not None and exc.response.status_code == 429:
        return "RATE_LIMITED"
    return "RPC_UNAVAILABLE"


_RETRYABLE_ERRORS = (
    request_errors.ConnectionError, request_errors.Timeout,
    request_errors.HTTPError, TimeoutError, _RetryableRpcError,
)


def _retryable_rpc_error(value: object) -> _RetryableRpcError | None:
    items = value if isinstance(value, list) else [value]
    errors = [item.get("error") for item in items if isinstance(item, dict) and "error" in item]
    if not errors or (isinstance(value, list) and len(errors) != len(value)):
        return None
    for error in errors:
        if not isinstance(error, dict):
            continue
        code, message = error.get("code"), str(error.get("message", "")).lower()
        if code in {429, -32005, -32016} or "rate limit" in message or "too many requests" in message:
            return _RetryableRpcError("RATE_LIMITED")
        if any(word in message for word in ("temporarily unavailable", "overloaded", "timeout")):
            return _RetryableRpcError("RPC_UNAVAILABLE")
    return None


def format_units(atomic_units: int, decimals: int, symbol: str) -> str:
    atomic = _non_negative(atomic_units)
    if decimals < 0 or decimals > 255:
        raise ValueError("Invalid asset decimals")
    value = Decimal(atomic).scaleb(-decimals)
    if value and decimals > 6 and value < Decimal("0.000001"):
        return f"<0.000001 {symbol}"
    if decimals > 6:
        value = value.quantize(Decimal("0.000001"), rounding=ROUND_DOWN)
    rendered = format(value, "f").rstrip("0").rstrip(".")
    return f"{rendered or '0'} {symbol}"


def snapshot_to_map(snapshot: NetworkSnapshot) -> dict[str, object]:
    return {
        "networkId": snapshot.network_id,
        "label": snapshot.label,
        "chainId": snapshot.chain_id,
        "status": snapshot.status.value,
        "blockNumber": str(snapshot.block_number) if snapshot.block_number is not None else "",
        "ethAtomic": str(snapshot.eth.atomic_units) if snapshot.eth else "",
        "ethValue": snapshot.eth.display_value if snapshot.eth else "Data unavailable",
        "usdcAtomic": str(snapshot.usdc.atomic_units) if snapshot.usdc else "",
        "usdcValue": snapshot.usdc.display_value if snapshot.usdc else "Data unavailable",
        "assets": [
            {
                "assetId": item.asset_id, "symbol": item.symbol,
                "atomic": str(item.atomic_units), "decimals": item.decimals,
                "display": item.display_value,
            }
            for item in snapshot.assets
        ],
        "updatedAt": snapshot.updated_at or "",
        "errorCode": snapshot.error_code or "",
    }


def _non_negative(value: int) -> int:
    result = int(value)
    if result < 0:
        raise ValueError("Public value must be non-negative")
    return result


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
