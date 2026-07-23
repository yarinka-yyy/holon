"""Bounded read-only Ethereum and Base public-data service."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN
from enum import Enum
from typing import Callable, Mapping, Protocol

from requests import exceptions as request_errors
from web3 import Web3


ETHEREUM_USDC = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
BASE_USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

USDC_ABI = (
    {
        "inputs": ({"internalType": "address", "name": "account", "type": "address"},),
        "name": "balanceOf",
        "outputs": ({"internalType": "uint256", "name": "", "type": "uint256"},),
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": (
            {"internalType": "address", "name": "owner", "type": "address"},
            {"internalType": "address", "name": "spender", "type": "address"},
        ),
        "name": "allowance",
        "outputs": ({"internalType": "uint256", "name": "", "type": "uint256"},),
        "stateMutability": "view",
        "type": "function",
    },
    {
        "inputs": (),
        "name": "decimals",
        "outputs": ({"internalType": "uint8", "name": "", "type": "uint8"},),
        "stateMutability": "view",
        "type": "function",
    },
)


class PublicDataStatus(str, Enum):
    LIVE = "LIVE"
    UNAVAILABLE = "UNAVAILABLE"
    SIMULATED = "SIMULATED"


@dataclass(frozen=True, slots=True)
class NetworkSpec:
    network_id: str
    label: str
    chain_id: int
    endpoint_env: str
    default_endpoint: str
    usdc_contract: str


NETWORKS: tuple[NetworkSpec, ...] = (
    NetworkSpec(
        "ethereum",
        "Ethereum",
        1,
        "HOLON_ETHEREUM_RPC_URL",
        "https://ethereum-rpc.publicnode.com",
        ETHEREUM_USDC,
    ),
    NetworkSpec(
        "base",
        "Base",
        8453,
        "HOLON_BASE_RPC_URL",
        "https://base-rpc.publicnode.com",
        BASE_USDC,
    ),
)
NETWORK_BY_ID = {network.network_id: network for network in NETWORKS}


@dataclass(frozen=True, slots=True)
class AssetBalance:
    symbol: str
    atomic_units: int
    decimals: int

    @property
    def display_value(self) -> str:
        return format_units(self.atomic_units, self.decimals, self.symbol)


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

    @classmethod
    def unavailable(cls, spec: NetworkSpec, code: str) -> NetworkSnapshot:
        return cls(
            spec.network_id,
            spec.label,
            spec.chain_id,
            PublicDataStatus.UNAVAILABLE,
            None,
            None,
            None,
            None,
            code,
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
    """Exposes only the fixed read methods required by M3.04."""

    def __init__(self, endpoint: str, timeout_seconds: float = 5.0) -> None:
        provider = Web3.HTTPProvider(
            endpoint,
            request_kwargs={"timeout": timeout_seconds},
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
        token = self._web3.eth.contract(
            address=Web3.to_checksum_address(contract), abi=USDC_ABI,
        )
        return int(token.functions.decimals().call())

    def token_balance(self, contract: str, address: str) -> int:
        token = self._web3.eth.contract(
            address=Web3.to_checksum_address(contract), abi=USDC_ABI,
        )
        return int(token.functions.balanceOf(Web3.to_checksum_address(address)).call())


RpcFactory = Callable[[str, str], PublicRpc]


class PublicDataService:
    """Reads allowlisted balances without touching vault authentication."""

    def __init__(
        self,
        rpc_factory: RpcFactory | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._rpc_factory = rpc_factory or self._default_rpc_factory
        self._environ = os.environ if environ is None else environ

    def refresh(
        self,
        profile_id: str,
        address: str,
        network_ids: tuple[str, ...] | None = None,
    ) -> PortfolioSnapshot:
        selected = network_ids or tuple(network.network_id for network in NETWORKS)
        if not selected or any(network_id not in NETWORK_BY_ID for network_id in selected):
            raise ValueError("Unsupported public-data network")
        snapshots = tuple(
            self._read_network(NETWORK_BY_ID[network_id], address)
            for network_id in selected
        )
        return PortfolioSnapshot(profile_id, address, snapshots)

    def _read_network(self, spec: NetworkSpec, address: str) -> NetworkSnapshot:
        endpoint = self._environ.get(spec.endpoint_env, spec.default_endpoint).strip()
        if not endpoint:
            return NetworkSnapshot.unavailable(spec, "RPC_UNAVAILABLE")
        attempts = 0
        while True:
            try:
                rpc = self._rpc_factory(spec.network_id, endpoint)
                observed_chain_id = rpc.chain_id()
                if observed_chain_id != spec.chain_id:
                    return NetworkSnapshot.unavailable(spec, "WRONG_CHAIN")
                block_number = _non_negative(rpc.block_number())
                native = _non_negative(rpc.native_balance(address))
                decimals = rpc.token_decimals(spec.usdc_contract)
                if decimals != 6:
                    return NetworkSnapshot.unavailable(spec, "TOKEN_METADATA_INVALID")
                usdc = _non_negative(rpc.token_balance(spec.usdc_contract, address))
                return NetworkSnapshot(
                    spec.network_id,
                    spec.label,
                    spec.chain_id,
                    PublicDataStatus.LIVE,
                    block_number,
                    AssetBalance("ETH", native, 18),
                    AssetBalance("USDC", usdc, decimals),
                    _utc_now(),
                )
            except _RETRYABLE_ERRORS:
                if attempts >= 1:
                    return NetworkSnapshot.unavailable(spec, "RPC_UNAVAILABLE")
                attempts += 1
            except (TypeError, ValueError, ArithmeticError):
                return NetworkSnapshot.unavailable(spec, "DATA_INVALID")
            except Exception:
                return NetworkSnapshot.unavailable(spec, "RPC_UNAVAILABLE")

    @staticmethod
    def _default_rpc_factory(_network_id: str, endpoint: str) -> PublicRpc:
        return Web3PublicRpc(endpoint)


_RETRYABLE_ERRORS = (
    request_errors.ConnectionError,
    request_errors.Timeout,
    request_errors.HTTPError,
    TimeoutError,
)


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
