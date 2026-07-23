"""Bounded USDC allowance inspection and exact revoke preparation."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from types import MappingProxyType
from typing import Protocol

from requests import exceptions as request_errors
from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from .model import ProfileSummary
from .public_data import NETWORK_BY_ID
from .transfer import (
    ALLOWLISTED_TOKEN_CONTRACTS,
    SigningPermit,
    UnsignedTransaction,
    Web3TransferRpc,
    _GasEstimateUnavailable,
    _RpcUnavailable,
    _TokenMetadataUnavailable,
    format_atomic_amount,
)

REVOKE_SCHEMA_VERSION = 1
REVOKE_ACTION_TYPE = "revoke"
REVOKE_LIFETIME = timedelta(minutes=5)
APPROVE_SELECTOR = bytes.fromhex("095ea7b3")
USDC_DECIMALS = 6
UINT256_MAX = 2**256 - 1
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
POSITIVE_ASCII_RE = re.compile(r"^[1-9][0-9]{0,77}$", re.ASCII)
ZERO_ADDRESS = "0x" + "00" * 20


@dataclass(frozen=True, slots=True)
class ApprovalRouteSpec:
    network_id: str
    network_label: str
    chain_id: int
    endpoint_env: str
    default_endpoint: str
    token_contract: str
    spender_env: str
    enabled_env: str
    fee_cap_env: str


def _route(network_id: str) -> ApprovalRouteSpec:
    network = NETWORK_BY_ID[network_id]
    prefix = f"HOLON_{network_id.upper()}_USDC_REVOKE"
    return ApprovalRouteSpec(
        network.network_id,
        network.label,
        network.chain_id,
        network.endpoint_env,
        network.default_endpoint,
        network.usdc_contract,
        f"{prefix}_SPENDER",
        f"{prefix}_ENABLED",
        f"{prefix}_MAX_TOTAL_FEE_WEI",
    )


APPROVAL_ROUTES = MappingProxyType({
    network_id: _route(network_id) for network_id in ("ethereum", "base")
})


class AllowanceStatus(str, Enum):
    LIVE = "LIVE"
    UNAVAILABLE = "UNAVAILABLE"
    NOT_CONFIGURED = "NOT_CONFIGURED"
    NO_ACTIVE_ALLOWANCE = "NO_ACTIVE_ALLOWANCE"


class RevokePreflightCode(str, Enum):
    INVALID_ROUTE = "INVALID_ROUTE"
    POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    FEE_LIMIT_EXCEEDED = "FEE_LIMIT_EXCEEDED"
    NO_ACTIVE_ALLOWANCE = "NO_ACTIVE_ALLOWANCE"
    RPC_UNAVAILABLE = "RPC_UNAVAILABLE"
    WRONG_CHAIN = "WRONG_CHAIN"
    TOKEN_METADATA_INVALID = "TOKEN_METADATA_INVALID"
    INSUFFICIENT_ETH = "INSUFFICIENT_ETH"
    GAS_ESTIMATE_FAILED = "GAS_ESTIMATE_FAILED"
    DATA_INVALID = "DATA_INVALID"


class RevokePolicyCode(str, Enum):
    POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    FEE_LIMIT_EXCEEDED = "FEE_LIMIT_EXCEEDED"
    ACTION_INVALID = "ACTION_INVALID"


class RevokeFlowState(str, Enum):
    LOCKED = "LOCKED"
    PREPARING = "PREPARING"
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"


class RevokePreflightError(RuntimeError):
    def __init__(self, code: RevokePreflightCode) -> None:
        super().__init__(code.value)
        self.code = code


class RevokeFlowError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RevokePolicy:
    enabled: Mapping[str, bool]
    spenders: Mapping[str, str | None]
    fee_caps: Mapping[str, int | None]

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None,
    ) -> RevokePolicy:
        source = os.environ if environ is None else environ
        return cls(
            MappingProxyType({
                network_id: source.get(route.enabled_env, "") == "1"
                for network_id, route in APPROVAL_ROUTES.items()
            }),
            MappingProxyType({
                network_id: _configured_spender(source.get(route.spender_env, ""))
                for network_id, route in APPROVAL_ROUTES.items()
            }),
            MappingProxyType({
                network_id: _positive_ascii(source.get(route.fee_cap_env, ""))
                for network_id, route in APPROVAL_ROUTES.items()
            }),
        )

    def spender_for(self, network_id: str, owner: str) -> str | None:
        spender = self.spenders.get(network_id)
        if spender is None or spender.lower() == owner.lower():
            return None
        return spender

    def fee_display(self, network_id: str) -> str:
        value = self.fee_caps.get(network_id)
        return "Not configured" if value is None else f"≤ {_format_wei(value)} ETH"

    def signing_available(self, network_id: str, owner: str) -> bool:
        return (
            self.enabled.get(network_id, False)
            and self.fee_caps.get(network_id) is not None
            and self.spender_for(network_id, owner) is not None
        )

    def evaluate(self, action: PreparedRevokeAction) -> RevokePolicyCode | None:
        spender = self.spender_for(action.network_id, action.sender)
        if (
            not self.enabled.get(action.network_id, False)
            or spender is None
            or spender != action.spender
        ):
            return RevokePolicyCode.POLICY_UNAVAILABLE
        fee_cap = self.fee_caps.get(action.network_id)
        if fee_cap is None:
            return RevokePolicyCode.POLICY_UNAVAILABLE
        if action.max_total_fee_wei > fee_cap:
            return RevokePolicyCode.FEE_LIMIT_EXCEEDED
        return None


@dataclass(frozen=True, slots=True)
class AllowanceSnapshot:
    network_id: str
    network_label: str
    chain_id: int
    owner: str
    token_contract: str
    spender: str | None
    allowance_atomic: int | None
    block_number: int | None
    status: AllowanceStatus
    updated_at: str | None


@dataclass(frozen=True, slots=True)
class PendingRevokeRequest:
    action_id: str
    profile_id: str
    network_id: str
    created_at: datetime
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class PreparedRevokeAction:
    schema_version: int
    action_type: str
    action_id: str
    profile_id: str
    account_label: str
    sender: str
    network_id: str
    network_label: str
    chain_id: int
    token: str
    token_contract: str
    spender: str
    allowance_before_atomic: int
    new_allowance_atomic: int
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
            "action_type": self.action_type,
            "action_id": self.action_id,
            "profile_id": self.profile_id,
            "account_label": self.account_label,
            "sender": self.sender,
            "network_id": self.network_id,
            "network_label": self.network_label,
            "chain_id": self.chain_id,
            "token": self.token,
            "token_contract": self.token_contract,
            "spender": self.spender,
            "allowance_before_atomic": self.allowance_before_atomic,
            "new_allowance_atomic": self.new_allowance_atomic,
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
            self.material_fields(), sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @property
    def calldata_hash(self) -> str:
        return hashlib.sha256(bytes.fromhex(self.transaction.data[2:])).hexdigest()


class ApprovalRpc(Protocol):
    def chain_id(self) -> int: ...
    def latest_block(self) -> tuple[int, int]: ...
    def native_balance(self, address: str) -> int: ...
    def token_decimals(self, contract: str) -> int: ...
    def allowance(self, contract: str, owner: str, spender: str) -> int: ...
    def pending_nonce(self, address: str) -> int: ...
    def max_priority_fee_per_gas(self) -> int: ...
    def estimate_gas(self, transaction: Mapping[str, object]) -> int: ...


RpcFactory = Callable[[str], ApprovalRpc]


class AllowanceReadService:
    def __init__(
        self,
        policy: RevokePolicy | None = None,
        rpc_factory: RpcFactory | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self.policy = policy or RevokePolicy.from_environment(self._environ)
        self._rpc_factory = rpc_factory or (lambda endpoint: Web3TransferRpc(endpoint))

    def inspect_all(self, profile: ProfileSummary) -> tuple[AllowanceSnapshot, ...]:
        return tuple(self.inspect(profile, network_id) for network_id in APPROVAL_ROUTES)

    def inspect(self, profile: ProfileSummary, network_id: str) -> AllowanceSnapshot:
        route = approval_route(network_id)
        spender = self.policy.spender_for(network_id, profile.address)
        if spender is None:
            return _snapshot(route, profile.address, None, None, None,
                             AllowanceStatus.NOT_CONFIGURED)
        endpoint = self._environ.get(route.endpoint_env, route.default_endpoint).strip()
        if not endpoint:
            return _snapshot(route, profile.address, spender, None, None,
                             AllowanceStatus.UNAVAILABLE)
        attempts = 0
        while True:
            try:
                rpc = self._rpc_factory(endpoint)
                if rpc.chain_id() != route.chain_id:
                    return _snapshot(route, profile.address, spender, None, None,
                                     AllowanceStatus.UNAVAILABLE)
                block_number, _base_fee = rpc.latest_block()
                if rpc.token_decimals(route.token_contract) != USDC_DECIMALS:
                    return _snapshot(route, profile.address, spender, None, None,
                                     AllowanceStatus.UNAVAILABLE)
                allowance = _uint256(
                    rpc.allowance(route.token_contract, profile.address, spender),
                )
                status = (
                    AllowanceStatus.NO_ACTIVE_ALLOWANCE
                    if allowance == 0 else AllowanceStatus.LIVE
                )
                return _snapshot(
                    route, profile.address, spender, allowance,
                    _non_negative(block_number), status,
                )
            except (_RpcUnavailable, request_errors.RequestException, TimeoutError):
                if attempts >= 1:
                    return _snapshot(route, profile.address, spender, None, None,
                                     AllowanceStatus.UNAVAILABLE)
                attempts += 1
            except Exception:
                return _snapshot(route, profile.address, spender, None, None,
                                 AllowanceStatus.UNAVAILABLE)


class RevokePreflightService:
    def __init__(
        self,
        policy: RevokePolicy | None = None,
        rpc_factory: RpcFactory | None = None,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._environ = os.environ if environ is None else environ
        self.policy = policy or RevokePolicy.from_environment(self._environ)
        self._rpc_factory = rpc_factory or (lambda endpoint: Web3TransferRpc(endpoint))

    def prepare(
        self, request: PendingRevokeRequest, profile: ProfileSummary,
    ) -> PreparedRevokeAction:
        route = approval_route(request.network_id)
        if request.profile_id != profile.profile_id:
            raise RevokePreflightError(RevokePreflightCode.DATA_INVALID)
        spender = self.policy.spender_for(route.network_id, profile.address)
        if not self.policy.signing_available(route.network_id, profile.address):
            raise RevokePreflightError(RevokePreflightCode.POLICY_UNAVAILABLE)
        assert spender is not None
        endpoint = self._environ.get(route.endpoint_env, route.default_endpoint).strip()
        if not endpoint:
            raise RevokePreflightError(RevokePreflightCode.RPC_UNAVAILABLE)
        attempts = 0
        while True:
            try:
                return self._prepare_once(
                    self._rpc_factory(endpoint), request, profile, route, spender,
                )
            except (_RpcUnavailable, request_errors.RequestException, TimeoutError) as error:
                if attempts >= 1:
                    raise RevokePreflightError(
                        RevokePreflightCode.RPC_UNAVAILABLE,
                    ) from error
                attempts += 1
            except _TokenMetadataUnavailable as error:
                raise RevokePreflightError(
                    RevokePreflightCode.TOKEN_METADATA_INVALID,
                ) from error
            except _GasEstimateUnavailable as error:
                raise RevokePreflightError(
                    RevokePreflightCode.GAS_ESTIMATE_FAILED,
                ) from error
            except (BadFunctionCallOutput, ContractLogicError) as error:
                raise RevokePreflightError(
                    RevokePreflightCode.GAS_ESTIMATE_FAILED,
                ) from error
            except RevokePreflightError:
                raise
            except Exception as error:
                raise RevokePreflightError(RevokePreflightCode.DATA_INVALID) from error

    def _prepare_once(
        self,
        rpc: ApprovalRpc,
        request: PendingRevokeRequest,
        profile: ProfileSummary,
        route: ApprovalRouteSpec,
        spender: str,
    ) -> PreparedRevokeAction:
        if rpc.chain_id() != route.chain_id:
            raise RevokePreflightError(RevokePreflightCode.WRONG_CHAIN)
        block_number, base_fee = rpc.latest_block()
        block_number = _non_negative(block_number)
        base_fee = _non_negative(base_fee)
        if rpc.token_decimals(route.token_contract) != USDC_DECIMALS:
            raise RevokePreflightError(RevokePreflightCode.TOKEN_METADATA_INVALID)
        allowance = _uint256(
            rpc.allowance(route.token_contract, profile.address, spender),
        )
        if allowance == 0:
            raise RevokePreflightError(RevokePreflightCode.NO_ACTIVE_ALLOWANCE)
        native_balance = _non_negative(rpc.native_balance(profile.address))
        nonce = _non_negative(rpc.pending_nonce(profile.address))
        priority_fee = _non_negative(rpc.max_priority_fee_per_gas())
        max_fee = 2 * base_fee + priority_fee
        if max_fee <= 0:
            raise RevokePreflightError(RevokePreflightCode.DATA_INVALID)
        calldata = encode_usdc_approve_zero(spender)
        estimate_transaction = {
            "from": profile.address,
            "to": route.token_contract,
            "value": 0,
            "data": calldata,
            "nonce": nonce,
            "type": 2,
            "chainId": route.chain_id,
            "maxFeePerGas": max_fee,
            "maxPriorityFeePerGas": priority_fee,
        }
        gas = _positive(rpc.estimate_gas(estimate_transaction))
        max_total_fee = gas * max_fee
        fee_cap = self.policy.fee_caps.get(route.network_id)
        if fee_cap is None:
            raise RevokePreflightError(RevokePreflightCode.POLICY_UNAVAILABLE)
        if max_total_fee > fee_cap:
            raise RevokePreflightError(RevokePreflightCode.FEE_LIMIT_EXCEEDED)
        if native_balance < max_total_fee:
            raise RevokePreflightError(RevokePreflightCode.INSUFFICIENT_ETH)
        transaction = UnsignedTransaction(
            2, route.chain_id, nonce, route.token_contract, 0, calldata,
            gas, max_fee, priority_fee,
        )
        return PreparedRevokeAction(
            REVOKE_SCHEMA_VERSION,
            REVOKE_ACTION_TYPE,
            request.action_id,
            profile.profile_id,
            profile.label,
            profile.address,
            route.network_id,
            route.network_label,
            route.chain_id,
            "USDC",
            route.token_contract,
            spender,
            allowance,
            0,
            USDC_DECIMALS,
            transaction,
            block_number,
            max_total_fee,
            request.created_at,
            request.expires_at,
        )


class RevokeFlowCoordinator:
    def __init__(
        self,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
        action_id_factory: Callable[[], str] = lambda: f"act-{uuid.uuid4()}",
    ) -> None:
        self._clock = clock
        self._action_id_factory = action_id_factory
        self._state = RevokeFlowState.LOCKED
        self._pending: PendingRevokeRequest | None = None
        self._current: PreparedRevokeAction | None = None
        self._accepted_digest = ""
        self._permit: SigningPermit | None = None
        self._terminal_ids: set[str] = set()

    @property
    def state(self) -> RevokeFlowState:
        return self._state

    @property
    def pending(self) -> PendingRevokeRequest | None:
        return self._pending

    @property
    def current(self) -> PreparedRevokeAction | None:
        return self._current

    @property
    def accepted_digest(self) -> str:
        return self._accepted_digest

    def begin(self, profile_id: str, network_id: str) -> PendingRevokeRequest:
        if self._state is not RevokeFlowState.LOCKED:
            raise RevokeFlowError("A revoke flow is already active")
        approval_route(network_id)
        action_id = self._action_id_factory()
        if action_id in self._terminal_ids:
            raise RevokeFlowError("Terminal action IDs cannot be reused")
        created_at = self._clock().astimezone(UTC)
        request = PendingRevokeRequest(
            action_id, profile_id, network_id, created_at,
            created_at + REVOKE_LIFETIME,
        )
        self._pending = request
        self._state = RevokeFlowState.PREPARING
        return request

    def still_pending(self, action_id: str, profile_id: str) -> bool:
        return (
            self._state is RevokeFlowState.PREPARING
            and self._pending is not None
            and self._pending.action_id == action_id
            and self._pending.profile_id == profile_id
        )

    def accept(self, action: PreparedRevokeAction) -> bool:
        pending = self._pending
        if self._state is not RevokeFlowState.PREPARING or pending is None:
            return False
        if (
            pending.action_id != action.action_id
            or pending.profile_id != action.profile_id
            or pending.network_id != action.network_id
            or pending.created_at != action.created_at
            or pending.expires_at != action.expires_at
            or self._clock().astimezone(UTC) >= action.expires_at
        ):
            self.close()
            return False
        self._pending = None
        self._current = action
        self._accepted_digest = action.digest
        self._state = RevokeFlowState.PREPARED
        return True

    def is_expired(self) -> bool:
        action = self._current
        if action is None or self._clock().astimezone(UTC) < action.expires_at:
            return False
        self.close()
        return True

    def begin_execution(
        self, action_id: str, digest: str, profile_id: str,
    ) -> SigningPermit | None:
        action = self._current
        if (
            self._state is not RevokeFlowState.PREPARED
            or action is None
            or action.action_id != action_id
            or action.profile_id != profile_id
            or digest != self._accepted_digest
            or action.digest != self._accepted_digest
            or self._clock().astimezone(UTC) >= action.expires_at
        ):
            self.close()
            return None
        self._permit = SigningPermit()
        self._state = RevokeFlowState.EXECUTING
        return self._permit

    def complete_execution(self, action_id: str) -> bool:
        if (
            self._state is not RevokeFlowState.EXECUTING
            or self._current is None
            or self._current.action_id != action_id
        ):
            self.close()
            return False
        self.close()
        return True

    def profile_changed(self, profile_id: str) -> bool:
        bound = (
            self._pending.profile_id if self._pending is not None
            else self._current.profile_id if self._current is not None
            else None
        )
        if bound is None or bound == profile_id:
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
        if self._permit is not None:
            self._permit.cancel()
        self._pending = None
        self._current = None
        self._accepted_digest = ""
        self._permit = None
        self._state = RevokeFlowState.LOCKED


def approval_route(network_id: str) -> ApprovalRouteSpec:
    try:
        return APPROVAL_ROUTES[network_id]
    except (KeyError, TypeError) as error:
        raise RevokePreflightError(RevokePreflightCode.INVALID_ROUTE) from error


def encode_usdc_approve_zero(spender: str) -> str:
    if not Web3.is_checksum_address(spender):
        raise ValueError("Invalid approve spender")
    spender_word = bytes.fromhex(spender[2:]).rjust(32, b"\x00")
    return "0x" + (APPROVE_SELECTOR + spender_word + bytes(32)).hex()


def allowance_snapshot_to_map(
    snapshot: AllowanceSnapshot, policy: RevokePolicy,
) -> dict[str, object]:
    allowance = snapshot.allowance_atomic
    return {
        "networkId": snapshot.network_id,
        "network": snapshot.network_label,
        "chainId": str(snapshot.chain_id),
        "owner": snapshot.owner,
        "contract": snapshot.token_contract,
        "shortContract": _short_address(snapshot.token_contract),
        "spender": snapshot.spender or "",
        "shortSpender": _short_address(snapshot.spender or ""),
        "allowanceAtomic": "" if allowance is None else str(allowance),
        "allowance": "Unavailable" if allowance is None else format_allowance(allowance),
        "block": "" if snapshot.block_number is None else str(snapshot.block_number),
        "status": snapshot.status.value,
        "statusLabel": _status_label(snapshot.status),
        "updatedAt": snapshot.updated_at or "",
        "revokeAvailable": (
            snapshot.status is AllowanceStatus.LIVE
            and allowance is not None
            and allowance > 0
            and policy.signing_available(snapshot.network_id, snapshot.owner)
        ),
        "policyConfigured": policy.signing_available(
            snapshot.network_id, snapshot.owner,
        ),
        "feeCap": policy.fee_display(snapshot.network_id),
    }


def revoke_action_to_map(action: PreparedRevokeAction) -> dict[str, object]:
    tx = action.transaction
    return {
        "actionId": action.action_id,
        "shortActionId": f"{action.action_id[:12]}…",
        "accountLabel": action.account_label,
        "owner": action.sender,
        "shortOwner": _short_address(action.sender),
        "spender": action.spender,
        "shortSpender": _short_address(action.spender),
        "network": action.network_label,
        "networkId": action.network_id,
        "chainId": str(action.chain_id),
        "token": action.token,
        "contract": action.token_contract,
        "shortContract": _short_address(action.token_contract),
        "allowanceBefore": format_allowance(action.allowance_before_atomic),
        "allowanceBeforeAtomic": str(action.allowance_before_atomic),
        "newAllowance": "0 USDC",
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


def format_allowance(value: int) -> str:
    allowance = _uint256(value)
    if allowance == UINT256_MAX:
        return "Unlimited USDC"
    return f"{format_atomic_amount(allowance, USDC_DECIMALS)} USDC"


def _configured_spender(value: object) -> str | None:
    candidate = value if isinstance(value, str) else ""
    if (
        ADDRESS_RE.fullmatch(candidate) is None
        or not Web3.is_checksum_address(candidate)
        or candidate == ZERO_ADDRESS
        or candidate.lower() in ALLOWLISTED_TOKEN_CONTRACTS
    ):
        return None
    return candidate


def _positive_ascii(value: object) -> int | None:
    candidate = value if isinstance(value, str) else ""
    if POSITIVE_ASCII_RE.fullmatch(candidate) is None:
        return None
    parsed = int(candidate)
    return parsed if parsed < 2**256 else None


def _snapshot(
    route: ApprovalRouteSpec,
    owner: str,
    spender: str | None,
    allowance: int | None,
    block: int | None,
    status: AllowanceStatus,
) -> AllowanceSnapshot:
    return AllowanceSnapshot(
        route.network_id,
        route.network_label,
        route.chain_id,
        owner,
        route.token_contract,
        spender,
        allowance,
        block,
        status,
        _timestamp(datetime.now(UTC)) if block is not None else None,
    )


def _status_label(status: AllowanceStatus) -> str:
    return {
        AllowanceStatus.LIVE: "Live",
        AllowanceStatus.UNAVAILABLE: "Unavailable",
        AllowanceStatus.NOT_CONFIGURED: "Not configured",
        AllowanceStatus.NO_ACTIVE_ALLOWANCE: "No active allowance",
    }[status]


def _uint256(value: object) -> int:
    result = int(value)
    if result < 0 or result >= 2**256:
        raise ValueError("Value is not uint256")
    return result


def _non_negative(value: object) -> int:
    result = int(value)
    if result < 0:
        raise ValueError("Value must be non-negative")
    return result


def _positive(value: object) -> int:
    result = int(value)
    if result <= 0:
        raise ValueError("Value must be positive")
    return result


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _format_wei(value: int) -> str:
    return format_atomic_amount(value, 18)


def _short_address(value: str) -> str:
    return "" if not value else f"{value[:8]}…{value[-6:]}"
