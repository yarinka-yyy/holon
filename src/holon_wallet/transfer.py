"""Exact unsigned ETH/USDC preparation for allowlisted MVP1 routes."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from enum import Enum
from threading import Event
from types import MappingProxyType
from typing import Mapping, Protocol

from requests import exceptions as request_errors
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError, Web3Exception

from .model import ProfileSummary
from .public_data import (
    BASE_USDC,
    ETHEREUM_USDC,
    NETWORK_BY_ID,
    USDC_ABI,
)

TRANSFER_SCHEMA_VERSION = 1
BASE_CHAIN_ID = 8453
BASE_NETWORK_ID = "base"
BASE_NETWORK_LABEL = "Base"
ETHEREUM_CHAIN_ID = 1
ETHEREUM_NETWORK_ID = "ethereum"
ETHEREUM_NETWORK_LABEL = "Ethereum"
ETH_ASSET_ID = "eth"
ETH_SYMBOL = "ETH"
ETH_DECIMALS = 18
USDC_ASSET_ID = "usdc"
USDC_SYMBOL = "USDC"
USDC_DECIMALS = 6
USDC_AMOUNT_ATOMIC = 1_000_000
ACTION_LIFETIME = timedelta(minutes=5)
TRANSFER_SELECTOR = bytes.fromhex("a9059cbb")
ZERO_ADDRESS = "0x" + "00" * 20
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
AMOUNT_RE = re.compile(r"^[0-9]+(?:[.,][0-9]+)?$")


@dataclass(frozen=True, slots=True)
class TransferRouteSpec:
    network_id: str
    network_label: str
    chain_id: int
    endpoint_env: str
    default_endpoint: str
    asset_id: str
    symbol: str
    decimals: int
    token_contract: str | None
    amount_cap_env: str


def _route(network_id: str, asset_id: str) -> TransferRouteSpec:
    network = NETWORK_BY_ID[network_id]
    symbol = asset_id.upper()
    return TransferRouteSpec(
        network.network_id,
        network.label,
        network.chain_id,
        network.endpoint_env,
        network.default_endpoint,
        asset_id,
        symbol,
        ETH_DECIMALS if asset_id == ETH_ASSET_ID else USDC_DECIMALS,
        None if asset_id == ETH_ASSET_ID else network.usdc_contract,
        (
            f"HOLON_{network_id.upper()}_ETH_MAX_AMOUNT_WEI"
            if asset_id == ETH_ASSET_ID
            else f"HOLON_{network_id.upper()}_USDC_MAX_AMOUNT_ATOMIC"
        ),
    )


TRANSFER_ROUTES = MappingProxyType({
    (network_id, asset_id): _route(network_id, asset_id)
    for network_id in (ETHEREUM_NETWORK_ID, BASE_NETWORK_ID)
    for asset_id in (ETH_ASSET_ID, USDC_ASSET_ID)
})
ALLOWLISTED_TOKEN_CONTRACTS = frozenset(
    contract.lower()
    for contract in (ETHEREUM_USDC, BASE_USDC)
)


class TransferFlowState(str, Enum):
    LOCKED = "LOCKED"
    PREPARING = "PREPARING"
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    SIGNING = "EXECUTING"


class TransferPreflightCode(str, Enum):
    INVALID_ROUTE = "INVALID_ROUTE"
    INVALID_AMOUNT = "INVALID_AMOUNT"
    AMOUNT_LIMIT_EXCEEDED = "AMOUNT_LIMIT_EXCEEDED"
    INVALID_RECIPIENT = "INVALID_RECIPIENT"
    RESERVED_RECIPIENT = "RESERVED_RECIPIENT"
    RPC_UNAVAILABLE = "RPC_UNAVAILABLE"
    WRONG_CHAIN = "WRONG_CHAIN"
    TOKEN_METADATA_INVALID = "TOKEN_METADATA_INVALID"
    INSUFFICIENT_USDC = "INSUFFICIENT_USDC"
    INSUFFICIENT_ETH = "INSUFFICIENT_ETH"
    GAS_ESTIMATE_FAILED = "GAS_ESTIMATE_FAILED"
    DATA_INVALID = "DATA_INVALID"


class TransferFlowError(RuntimeError):
    """A transfer flow transition was rejected."""


class SigningPermit:
    """Thread-safe cancellation signal for one critical execution attempt."""

    def __init__(self) -> None:
        self._cancelled = Event()

    @property
    def cancelled(self) -> bool:
        return self._cancelled.is_set()

    def cancel(self) -> None:
        self._cancelled.set()


class TransferPreflightError(RuntimeError):
    """A safe, non-secret preflight failure."""

    def __init__(self, code: TransferPreflightCode) -> None:
        super().__init__(code.value)
        self.code = code


class _RpcUnavailable(RuntimeError):
    pass


class _GasEstimateUnavailable(RuntimeError):
    pass


class _TokenMetadataUnavailable(RuntimeError):
    pass


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_action_id() -> str:
    return f"act-{uuid.uuid4()}"


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class PendingTransferRequest:
    action_id: str
    profile_id: str
    created_at: datetime
    expires_at: datetime
    network_id: str = BASE_NETWORK_ID
    asset_id: str = USDC_ASSET_ID
    amount_atomic: int = USDC_AMOUNT_ATOMIC


@dataclass(frozen=True, slots=True)
class UnsignedTransaction:
    transaction_type: int
    chain_id: int
    nonce: int
    to: str
    value: int
    data: str
    gas: int
    max_fee_per_gas: int
    max_priority_fee_per_gas: int

    def material_fields(self) -> dict[str, object]:
        return {
            "type": self.transaction_type,
            "chain_id": self.chain_id,
            "nonce": self.nonce,
            "to": self.to,
            "value": self.value,
            "data": self.data,
            "gas": self.gas,
            "max_fee_per_gas": self.max_fee_per_gas,
            "max_priority_fee_per_gas": self.max_priority_fee_per_gas,
        }


@dataclass(frozen=True, slots=True)
class TransferPreflightSnapshot:
    block_number: int
    native_balance_wei: int
    token_balance_atomic: int
    token_decimals: int
    pending_nonce: int
    base_fee_per_gas: int
    max_priority_fee_per_gas: int
    gas_estimate: int


@dataclass(frozen=True, slots=True)
class PreparedTransferAction:
    schema_version: int
    action_id: str
    profile_id: str
    account_label: str
    sender: str
    recipient: str
    network_id: str
    network_label: str
    chain_id: int
    asset_id: str
    token: str
    token_contract: str | None
    amount_atomic: int
    decimals: int
    transaction: UnsignedTransaction
    block_number: int
    max_total_fee_wei: int
    created_at: datetime
    expires_at: datetime
    simulation: bool = False

    def material_fields(self) -> dict[str, object]:
        return {
            "schema_version": self.schema_version,
            "action_id": self.action_id,
            "profile_id": self.profile_id,
            "account_label": self.account_label,
            "sender": self.sender,
            "recipient": self.recipient,
            "network_id": self.network_id,
            "network_label": self.network_label,
            "chain_id": self.chain_id,
            "asset_id": self.asset_id,
            "token": self.token,
            "token_contract": self.token_contract,
            "amount_atomic": self.amount_atomic,
            "decimals": self.decimals,
            "transaction": self.transaction.material_fields(),
            "block_number": self.block_number,
            "max_total_fee_wei": self.max_total_fee_wei,
            "created_at": _timestamp(self.created_at),
            "expires_at": _timestamp(self.expires_at),
            "simulation": self.simulation,
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.material_fields(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def calldata_hash(self) -> str:
        return hashlib.sha256(bytes.fromhex(self.transaction.data[2:])).hexdigest()


class TransferRpc(Protocol):
    def chain_id(self) -> int: ...

    def latest_block(self) -> tuple[int, int]: ...

    def native_balance(self, address: str) -> int: ...

    def token_decimals(self, contract: str) -> int: ...

    def token_balance(self, contract: str, address: str) -> int: ...

    def pending_nonce(self, address: str) -> int: ...

    def max_priority_fee_per_gas(self) -> int: ...

    def estimate_gas(self, transaction: Mapping[str, object]) -> int: ...


class Web3TransferRpc:
    """Provides only the read-only calls needed to prepare one transaction."""

    def __init__(self, endpoint: str, timeout_seconds: float = 5.0) -> None:
        provider = Web3.HTTPProvider(
            endpoint,
            request_kwargs={"timeout": timeout_seconds},
            exception_retry_configuration=None,
        )
        self._web3 = Web3(provider)

    def chain_id(self) -> int:
        return int(self._call(lambda: self._web3.eth.chain_id))

    def latest_block(self) -> tuple[int, int]:
        block = self._call(lambda: self._web3.eth.get_block("latest"))
        try:
            return int(block["number"]), int(block["baseFeePerGas"])
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError("Invalid block fee data") from error

    def native_balance(self, address: str) -> int:
        return int(self._call(lambda: self._web3.eth.get_balance(address)))

    def token_decimals(self, contract: str) -> int:
        token = self._web3.eth.contract(address=contract, abi=USDC_ABI)
        try:
            return int(self._call(lambda: token.functions.decimals().call()))
        except BadFunctionCallOutput as error:
            raise _TokenMetadataUnavailable from error

    def token_balance(self, contract: str, address: str) -> int:
        token = self._web3.eth.contract(address=contract, abi=USDC_ABI)
        return int(self._call(lambda: token.functions.balanceOf(address).call()))

    def pending_nonce(self, address: str) -> int:
        return int(self._call(lambda: self._web3.eth.get_transaction_count(address, "pending")))

    def max_priority_fee_per_gas(self) -> int:
        return int(self._call(lambda: self._web3.eth.max_priority_fee))

    def estimate_gas(self, transaction: Mapping[str, object]) -> int:
        try:
            return int(self._call(lambda: self._web3.eth.estimate_gas(dict(transaction))))
        except ContractLogicError as error:
            raise _GasEstimateUnavailable from error

    @staticmethod
    def _call(call: Callable[[], object]) -> object:
        try:
            return call()
        except (ContractLogicError, BadFunctionCallOutput):
            raise
        except (*_TRANSPORT_ERRORS, Web3Exception) as error:
            raise _RpcUnavailable from error


RpcFactory = Callable[[str], TransferRpc]


class TransferPreflightService:
    """Builds one exact unsigned transaction for an allowlisted route."""

    def __init__(
        self,
        rpc_factory: RpcFactory | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._rpc_factory = rpc_factory or self._default_rpc_factory
        self._environ = os.environ if environ is None else environ

    def prepare(
        self,
        request: PendingTransferRequest,
        profile: ProfileSummary,
        recipient: str,
    ) -> PreparedTransferAction:
        if request.profile_id != profile.profile_id:
            raise TransferPreflightError(TransferPreflightCode.DATA_INVALID)
        try:
            route = transfer_route(request.network_id, request.asset_id)
        except TransferPreflightError:
            raise
        if (
            type(request.amount_atomic) is not int
            or request.amount_atomic <= 0
            or request.amount_atomic >= 2**256
        ):
            raise TransferPreflightError(TransferPreflightCode.INVALID_AMOUNT)
        normalized = normalize_recipient(recipient, profile.address)
        endpoint = self._environ.get(route.endpoint_env, route.default_endpoint).strip()
        if not endpoint:
            raise TransferPreflightError(TransferPreflightCode.RPC_UNAVAILABLE)
        attempts = 0
        while True:
            try:
                return self._prepare_once(
                    self._rpc_factory(endpoint), request, profile, normalized, route,
                )
            except _RpcUnavailable as error:
                if attempts >= 1:
                    raise TransferPreflightError(
                        TransferPreflightCode.RPC_UNAVAILABLE,
                    ) from error
                attempts += 1
            except _TRANSPORT_ERRORS as error:
                if attempts >= 1:
                    raise TransferPreflightError(
                        TransferPreflightCode.RPC_UNAVAILABLE,
                    ) from error
                attempts += 1
            except _TokenMetadataUnavailable as error:
                raise TransferPreflightError(
                    TransferPreflightCode.TOKEN_METADATA_INVALID,
                ) from error
            except _GasEstimateUnavailable as error:
                raise TransferPreflightError(
                    TransferPreflightCode.GAS_ESTIMATE_FAILED,
                ) from error
            except BadFunctionCallOutput as error:
                raise TransferPreflightError(
                    TransferPreflightCode.TOKEN_METADATA_INVALID,
                ) from error
            except ContractLogicError as error:
                raise TransferPreflightError(
                    TransferPreflightCode.GAS_ESTIMATE_FAILED,
                ) from error
            except TransferPreflightError:
                raise
            except (ArithmeticError, KeyError, TypeError, ValueError) as error:
                raise TransferPreflightError(TransferPreflightCode.DATA_INVALID) from error
            except Exception as error:
                raise TransferPreflightError(
                    TransferPreflightCode.RPC_UNAVAILABLE,
                ) from error

    def quote_maximum_native(
        self,
        profile: ProfileSummary,
        network_id: str,
        recipient: str,
    ) -> int:
        route = transfer_route(network_id, ETH_ASSET_ID)
        normalized = normalize_recipient(recipient, profile.address)
        endpoint = self._environ.get(route.endpoint_env, route.default_endpoint).strip()
        if not endpoint:
            raise TransferPreflightError(TransferPreflightCode.RPC_UNAVAILABLE)
        attempts = 0
        while True:
            try:
                return self._quote_maximum_native_once(
                    self._rpc_factory(endpoint), profile, normalized, route,
                )
            except (_RpcUnavailable, *_TRANSPORT_ERRORS) as error:
                if attempts >= 1:
                    raise TransferPreflightError(
                        TransferPreflightCode.RPC_UNAVAILABLE,
                    ) from error
                attempts += 1
            except _GasEstimateUnavailable as error:
                raise TransferPreflightError(
                    TransferPreflightCode.GAS_ESTIMATE_FAILED,
                ) from error
            except TransferPreflightError:
                raise
            except (ArithmeticError, KeyError, TypeError, ValueError) as error:
                raise TransferPreflightError(TransferPreflightCode.DATA_INVALID) from error
            except Exception as error:
                raise TransferPreflightError(
                    TransferPreflightCode.RPC_UNAVAILABLE,
                ) from error

    @staticmethod
    def _quote_maximum_native_once(
        rpc: TransferRpc,
        profile: ProfileSummary,
        recipient: str,
        route: TransferRouteSpec,
    ) -> int:
        if rpc.chain_id() != route.chain_id:
            raise TransferPreflightError(TransferPreflightCode.WRONG_CHAIN)
        block_number, base_fee = rpc.latest_block()
        _non_negative(block_number)
        balance = _non_negative(rpc.native_balance(profile.address))
        nonce = _non_negative(rpc.pending_nonce(profile.address))
        priority_fee = _non_negative(rpc.max_priority_fee_per_gas())
        base_fee = _non_negative(base_fee)
        max_fee = 2 * base_fee + priority_fee
        if max_fee <= 0:
            raise TransferPreflightError(TransferPreflightCode.DATA_INVALID)
        provisional_value = balance - 21_000 * max_fee
        if provisional_value <= 0:
            raise TransferPreflightError(TransferPreflightCode.INSUFFICIENT_ETH)
        transaction = {
            "from": profile.address,
            "to": recipient,
            "value": provisional_value,
            "data": "0x",
            "nonce": nonce,
            "type": 2,
            "chainId": route.chain_id,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
        }
        first_estimate = _positive(rpc.estimate_gas(transaction))
        first_reserve_gas = first_estimate + max(1_000, (first_estimate + 9) // 10)
        amount = balance - first_reserve_gas * max_fee
        if amount <= 0:
            raise TransferPreflightError(TransferPreflightCode.INSUFFICIENT_ETH)
        transaction["value"] = amount
        final_estimate = _positive(rpc.estimate_gas(transaction))
        estimate = max(first_estimate, final_estimate)
        reserve_gas = estimate + max(1_000, (estimate + 9) // 10)
        amount = balance - reserve_gas * max_fee
        if amount <= 0:
            raise TransferPreflightError(TransferPreflightCode.INSUFFICIENT_ETH)
        return amount

    def _prepare_once(
        self,
        rpc: TransferRpc,
        request: PendingTransferRequest,
        profile: ProfileSummary,
        recipient: str,
        route: TransferRouteSpec,
    ) -> PreparedTransferAction:
        if rpc.chain_id() != route.chain_id:
            raise TransferPreflightError(TransferPreflightCode.WRONG_CHAIN)
        block_number, base_fee = rpc.latest_block()
        native_balance = _non_negative(rpc.native_balance(profile.address))
        token_balance = 0
        decimals = route.decimals
        if route.token_contract is not None:
            decimals = rpc.token_decimals(route.token_contract)
            if decimals != route.decimals:
                raise TransferPreflightError(TransferPreflightCode.TOKEN_METADATA_INVALID)
            token_balance = _non_negative(
                rpc.token_balance(route.token_contract, profile.address)
            )
            if token_balance < request.amount_atomic:
                raise TransferPreflightError(TransferPreflightCode.INSUFFICIENT_USDC)
        nonce = _non_negative(rpc.pending_nonce(profile.address))
        priority_fee = _non_negative(rpc.max_priority_fee_per_gas())
        block_number = _non_negative(block_number)
        base_fee = _non_negative(base_fee)
        max_fee = 2 * base_fee + priority_fee
        if max_fee <= 0:
            raise TransferPreflightError(TransferPreflightCode.DATA_INVALID)
        calldata = (
            "0x"
            if route.token_contract is None
            else encode_usdc_transfer(recipient, request.amount_atomic)
        )
        estimate_transaction = {
            "from": profile.address,
            "to": recipient if route.token_contract is None else route.token_contract,
            "value": request.amount_atomic if route.token_contract is None else 0,
            "data": calldata,
            "nonce": nonce,
            "type": 2,
            "chainId": route.chain_id,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
        }
        gas = _positive(rpc.estimate_gas(estimate_transaction))
        max_total_fee = gas * max_fee
        required_native = max_total_fee + (
            request.amount_atomic if route.token_contract is None else 0
        )
        if native_balance < required_native:
            raise TransferPreflightError(TransferPreflightCode.INSUFFICIENT_ETH)
        snapshot = TransferPreflightSnapshot(
            block_number,
            native_balance,
            token_balance,
            decimals,
            nonce,
            base_fee,
            priority_fee,
            gas,
        )
        return _action_from_snapshot(
            request, profile, recipient, calldata, snapshot, route,
        )

    @staticmethod
    def _default_rpc_factory(endpoint: str) -> TransferRpc:
        return Web3TransferRpc(endpoint)


class TransferFlowCoordinator:
    """Owns one transient preflight request or prepared transfer."""

    def __init__(
        self,
        clock: Callable[[], datetime] = _utc_now,
        action_id_factory: Callable[[], str] = _new_action_id,
    ) -> None:
        self._clock = clock
        self._action_id_factory = action_id_factory
        self._state = TransferFlowState.LOCKED
        self._pending: PendingTransferRequest | None = None
        self._current: PreparedTransferAction | None = None
        self._accepted_digest = ""
        self._signing_permit: SigningPermit | None = None
        self._terminal_ids: set[str] = set()

    @property
    def state(self) -> TransferFlowState:
        return self._state

    @property
    def pending(self) -> PendingTransferRequest | None:
        return self._pending

    @property
    def current(self) -> PreparedTransferAction | None:
        return self._current

    @property
    def accepted_digest(self) -> str:
        return self._accepted_digest

    def begin(
        self,
        profile_id: str,
        network_id: str = BASE_NETWORK_ID,
        asset_id: str = USDC_ASSET_ID,
        amount_atomic: int = USDC_AMOUNT_ATOMIC,
    ) -> PendingTransferRequest:
        if self._state is not TransferFlowState.LOCKED:
            raise TransferFlowError("A transfer flow is already active")
        transfer_route(network_id, asset_id)
        if type(amount_atomic) is not int or amount_atomic <= 0 or amount_atomic >= 2**256:
            raise TransferPreflightError(TransferPreflightCode.INVALID_AMOUNT)
        action_id = self._action_id_factory()
        if action_id in self._terminal_ids:
            raise TransferFlowError("Terminal action IDs cannot be reused")
        created_at = self._clock().astimezone(UTC)
        request = PendingTransferRequest(
            action_id,
            profile_id,
            created_at,
            created_at + ACTION_LIFETIME,
            network_id,
            asset_id,
            amount_atomic,
        )
        self._pending = request
        self._state = TransferFlowState.PREPARING
        return request

    def accept(self, action: PreparedTransferAction) -> bool:
        pending = self._pending
        if self._state is not TransferFlowState.PREPARING or pending is None:
            return False
        if pending.action_id != action.action_id:
            return False
        if (
            pending.profile_id != action.profile_id
            or pending.network_id != action.network_id
            or pending.asset_id != action.asset_id
            or pending.amount_atomic != action.amount_atomic
            or pending.created_at != action.created_at
            or pending.expires_at != action.expires_at
            or self._clock().astimezone(UTC) >= action.expires_at
        ):
            self.close()
            return False
        self._pending = None
        self._current = action
        self._accepted_digest = action.digest
        self._state = TransferFlowState.PREPARED
        return True

    def still_pending(self, action_id: str, profile_id: str) -> bool:
        pending = self._pending
        return (
            self._state is TransferFlowState.PREPARING
            and pending is not None
            and pending.action_id == action_id
            and pending.profile_id == profile_id
        )

    def is_expired(self) -> bool:
        action = self._current
        if action is None:
            return False
        if self._clock().astimezone(UTC) < action.expires_at:
            return False
        self.close()
        return True

    def validate(self, action_id: str, digest: str, profile_id: str) -> bool:
        action = self._current
        if (
            self._state is not TransferFlowState.PREPARED
            or action is None
            or action.action_id != action_id
            or action.profile_id != profile_id
            or digest != self._accepted_digest
            or action.digest != self._accepted_digest
            or self._clock().astimezone(UTC) >= action.expires_at
        ):
            self.close()
            return False
        return True

    def begin_signing(
        self, action_id: str, digest: str, profile_id: str,
    ) -> SigningPermit | None:
        return self.begin_execution(action_id, digest, profile_id)

    def begin_execution(
        self, action_id: str, digest: str, profile_id: str,
    ) -> SigningPermit | None:
        if not self.validate(action_id, digest, profile_id):
            return None
        permit = SigningPermit()
        self._signing_permit = permit
        self._state = TransferFlowState.EXECUTING
        return permit

    def complete_signing(self, action_id: str) -> bool:
        return self.complete_execution(action_id)

    def complete_execution(self, action_id: str) -> bool:
        action = self._current
        if (
            self._state is not TransferFlowState.EXECUTING
            or action is None
            or action.action_id != action_id
        ):
            self.close()
            return False
        self.close()
        return True

    def profile_changed(self, profile_id: str) -> bool:
        active_profile_id = (
            self._pending.profile_id if self._pending is not None
            else self._current.profile_id if self._current is not None
            else None
        )
        if active_profile_id is None or active_profile_id == profile_id:
            return False
        self.close()
        return True

    def close(self) -> None:
        action_id = (
            self._pending.action_id if self._pending is not None
            else self._current.action_id if self._current is not None
            else None
        )
        if action_id is not None:
            self._terminal_ids.add(action_id)
        if self._signing_permit is not None:
            self._signing_permit.cancel()
        self._pending = None
        self._current = None
        self._accepted_digest = ""
        self._signing_permit = None
        self._state = TransferFlowState.LOCKED


def transfer_route(network_id: str, asset_id: str) -> TransferRouteSpec:
    try:
        return TRANSFER_ROUTES[(network_id, asset_id)]
    except (KeyError, TypeError) as error:
        raise TransferPreflightError(TransferPreflightCode.INVALID_ROUTE) from error


def parse_transfer_amount(value: str, decimals: int) -> tuple[int, str]:
    candidate = value if isinstance(value, str) else ""
    if (
        type(decimals) is not int
        or decimals < 0
        or "." in candidate and "," in candidate
        or AMOUNT_RE.fullmatch(candidate) is None
    ):
        raise TransferPreflightError(TransferPreflightCode.INVALID_AMOUNT)
    normalized = candidate.replace(",", ".")
    whole, separator, fraction = normalized.partition(".")
    if len(fraction) > decimals:
        raise TransferPreflightError(TransferPreflightCode.INVALID_AMOUNT)
    atomic = int(whole) * 10**decimals
    if separator:
        atomic += int(fraction.ljust(decimals, "0"))
    if atomic <= 0 or atomic >= 2**256:
        raise TransferPreflightError(TransferPreflightCode.INVALID_AMOUNT)
    return atomic, format_atomic_amount(atomic, decimals)


def normalize_recipient(value: str, sender: str) -> str:
    candidate = value.strip() if isinstance(value, str) else ""
    if ADDRESS_RE.fullmatch(candidate) is None or not Web3.is_address(candidate):
        raise TransferPreflightError(TransferPreflightCode.INVALID_RECIPIENT)
    body = candidate[2:]
    if not (body.islower() or body.isupper()) and not Web3.is_checksum_address(candidate):
        raise TransferPreflightError(TransferPreflightCode.INVALID_RECIPIENT)
    normalized = Web3.to_checksum_address(candidate)
    reserved = {
        ZERO_ADDRESS.lower(),
        sender.lower(),
        *ALLOWLISTED_TOKEN_CONTRACTS,
    }
    if normalized.lower() in reserved:
        raise TransferPreflightError(TransferPreflightCode.RESERVED_RECIPIENT)
    return normalized


def encode_usdc_transfer(recipient: str, amount_atomic: int) -> str:
    normalized = Web3.to_checksum_address(recipient)
    if amount_atomic <= 0 or amount_atomic >= 2**256:
        raise ValueError("Invalid transfer amount")
    address_word = bytes.fromhex(normalized[2:]).rjust(32, b"\x00")
    amount_word = amount_atomic.to_bytes(32, "big")
    return "0x" + (TRANSFER_SELECTOR + address_word + amount_word).hex()


def transfer_action_to_map(action: PreparedTransferAction) -> dict[str, object]:
    tx = action.transaction
    return {
        "actionId": action.action_id,
        "shortActionId": f"{action.action_id[:12]}…",
        "accountLabel": action.account_label,
        "sender": action.sender,
        "shortSender": _short_address(action.sender),
        "recipient": action.recipient,
        "shortRecipient": _short_address(action.recipient),
        "network": action.network_label,
        "networkId": action.network_id,
        "chainId": str(action.chain_id),
        "assetId": action.asset_id,
        "token": action.token,
        "amount": f"{format_atomic_amount(action.amount_atomic, action.decimals)} {action.token}",
        "amountValue": format_atomic_amount(action.amount_atomic, action.decimals),
        "amountAtomic": str(action.amount_atomic),
        "contract": action.token_contract or "",
        "shortContract": (
            _short_address(action.token_contract) if action.token_contract else "Native asset"
        ),
        "transactionTarget": tx.to,
        "shortTransactionTarget": _short_address(tx.to),
        "nativeValueWei": str(tx.value),
        "calldataHash": action.calldata_hash,
        "nonce": str(tx.nonce),
        "gas": str(tx.gas),
        "block": str(action.block_number),
        "maxFeePerGas": str(tx.max_fee_per_gas),
        "maxPriorityFeePerGas": str(tx.max_priority_fee_per_gas),
        "maxTotalFeeWei": str(action.max_total_fee_wei),
        "maxFeeDisplay": f"≤ {_format_wei(action.max_total_fee_wei)} ETH",
        "expiresAt": action.expires_at.strftime("%H:%M:%S UTC"),
        "digest": action.digest,
        "shortDigest": f"{action.digest[:12]}…{action.digest[-8:]}",
        "simulation": action.simulation,
    }


def _action_from_snapshot(
    request: PendingTransferRequest,
    profile: ProfileSummary,
    recipient: str,
    calldata: str,
    snapshot: TransferPreflightSnapshot,
    route: TransferRouteSpec,
) -> PreparedTransferAction:
    max_fee = 2 * snapshot.base_fee_per_gas + snapshot.max_priority_fee_per_gas
    transaction = UnsignedTransaction(
        2,
        route.chain_id,
        snapshot.pending_nonce,
        recipient if route.token_contract is None else route.token_contract,
        request.amount_atomic if route.token_contract is None else 0,
        calldata,
        snapshot.gas_estimate,
        max_fee,
        snapshot.max_priority_fee_per_gas,
    )
    return PreparedTransferAction(
        TRANSFER_SCHEMA_VERSION,
        request.action_id,
        profile.profile_id,
        profile.label,
        profile.address,
        recipient,
        route.network_id,
        route.network_label,
        route.chain_id,
        route.asset_id,
        route.symbol,
        route.token_contract,
        request.amount_atomic,
        route.decimals,
        transaction,
        snapshot.block_number,
        snapshot.gas_estimate * max_fee,
        request.created_at,
        request.expires_at,
    )


def _non_negative(value: int) -> int:
    result = int(value)
    if result < 0:
        raise ValueError("Value must be non-negative")
    return result


def _positive(value: int) -> int:
    result = int(value)
    if result <= 0:
        raise ValueError("Value must be positive")
    return result


def _short_address(value: str) -> str:
    return f"{value[:8]}…{value[-6:]}"


def _format_wei(value: int) -> str:
    rendered = format(Decimal(value).scaleb(-18), "f").rstrip("0").rstrip(".")
    return rendered or "0"


def format_atomic_amount(value: int, decimals: int) -> str:
    whole, fraction = divmod(int(value), 10**decimals)
    if fraction == 0:
        return str(whole)
    return f"{whole}.{fraction:0{decimals}d}".rstrip("0")


_TRANSPORT_ERRORS = (
    request_errors.ConnectionError,
    request_errors.Timeout,
    request_errors.HTTPError,
    TimeoutError,
)
