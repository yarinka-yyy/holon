"""Single-use allowlisted signing, broadcast, and public receipt tracking."""

from __future__ import annotations

import hmac
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from threading import Event
from typing import Protocol

from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes
from requests import exceptions as request_errors
from web3 import Web3
from web3.exceptions import (
    BadFunctionCallOutput,
    ContractLogicError,
    TransactionNotFound,
    Web3Exception,
)

from .approval import (
    REVOKE_ACTION_TYPE,
    PreparedRevokeAction,
    RevokePolicy,
    RevokePolicyCode,
    approval_route,
    encode_usdc_approve_zero,
)
from .history import (
    HistoryStatus,
    HistoryStore,
    HistoryUnavailableError,
    HistoryValidationError,
    WalletHistoryRecord,
)
from .public_data import USDC_ABI
from .signer import (
    OfflineSigningCode,
    OfflineSigningPolicy,
    decoded_transaction_matches,
    transaction_dict,
    validate_signing_action,
)
from .storage import StorageError
from .transfer import (
    BASE_NETWORK_ID,
    ETH_ASSET_ID,
    TRANSFER_ROUTES,
    PreparedTransferAction,
    SigningPermit,
    TransferRouteSpec,
    encode_usdc_transfer,
    transfer_route,
)
from .vault import AuthenticationFailedError, VaultRepository, VaultUnavailableError
from .wallet_crypto import InvalidSecretError, private_key_bytes, rederive

BROADCAST_ENABLED_ENV = "HOLON_BASE_BROADCAST_ENABLED"
BASE_RPC_ENV = "HOLON_BASE_RPC_URL"
DEFAULT_BASE_RPC_URL = "https://base-rpc.publicnode.com"
BROADCAST_ENABLED_ENVS = {
    "base": BROADCAST_ENABLED_ENV,
    "ethereum": "HOLON_ETHEREUM_BROADCAST_ENABLED",
}
TRANSFER_EVENT_TOPIC = Web3.to_hex(
    Web3.keccak(text="Transfer(address,address,uint256)"),
)
APPROVAL_EVENT_TOPIC = Web3.to_hex(
    Web3.keccak(text="Approval(address,address,uint256)"),
)
PreparedTransactionAction = PreparedTransferAction | PreparedRevokeAction


class MainnetTransferCode(str, Enum):
    CONFIRMED = "CONFIRMED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"
    FAILED = "FAILED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    FEE_LIMIT_EXCEEDED = "FEE_LIMIT_EXCEEDED"
    AMOUNT_LIMIT_EXCEEDED = "AMOUNT_LIMIT_EXCEEDED"
    ACTION_INVALID = "ACTION_INVALID"
    ACTION_EXPIRED = "ACTION_EXPIRED"
    REVALIDATION_FAILED = "REVALIDATION_FAILED"
    HISTORY_UNAVAILABLE = "HISTORY_UNAVAILABLE"
    CANCELLED = "CANCELLED"
    SIGNING_FAILED = "SIGNING_FAILED"


@dataclass(frozen=True, slots=True)
class MainnetBroadcastPolicy:
    enabled: bool
    fee_policy: OfflineSigningPolicy
    network_enabled: Mapping[str, bool] | None = None
    fee_policies: Mapping[str, OfflineSigningPolicy] | None = None
    amount_limits: Mapping[tuple[str, str], int | None] | None = None

    @classmethod
    def from_environment(
        cls, environ: Mapping[str, str] | None = None,
    ) -> MainnetBroadcastPolicy:
        source = os.environ if environ is None else environ
        enabled = {
            network_id: source.get(env_name, "").strip() == "1"
            for network_id, env_name in BROADCAST_ENABLED_ENVS.items()
        }
        fee_policies = {
            network_id: OfflineSigningPolicy.from_environment(source, network_id)
            for network_id in BROADCAST_ENABLED_ENVS
        }
        amount_limits = {
            key: _positive_environment_value(source, route.amount_cap_env)
            for key, route in TRANSFER_ROUTES.items()
        }
        return cls(
            enabled[BASE_NETWORK_ID],
            fee_policies[BASE_NETWORK_ID],
            enabled,
            fee_policies,
            amount_limits,
        )

    @property
    def available(self) -> bool:
        return (
            self._network_enabled(BASE_NETWORK_ID)
            and self._fee_policy(BASE_NETWORK_ID).available
            and self._amount_limit(BASE_NETWORK_ID, "usdc") is not None
        )

    @property
    def display(self) -> str:
        return self.fee_policy.display

    def display_for(self, action: PreparedTransferAction) -> str:
        return self._fee_policy(action.network_id).display

    def amount_display_for(self, action: PreparedTransferAction) -> str:
        limit = self._amount_limit(action.network_id, action.asset_id)
        if limit is None:
            return "Not configured"
        return str(limit)

    def draft_amount_code(
        self, network_id: str, asset_id: str, amount_atomic: int,
    ) -> MainnetTransferCode | None:
        limit = self._amount_limit(network_id, asset_id)
        if limit is not None and amount_atomic > limit:
            return MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED
        return None

    def maximum_draft_amount(
        self,
        network_id: str,
        asset_id: str,
        available_atomic: int,
    ) -> int | None:
        if type(available_atomic) is not int or available_atomic <= 0:
            return None
        candidate = available_atomic
        amount_limit = self._amount_limit(network_id, asset_id)
        if amount_limit is not None:
            candidate = min(candidate, amount_limit)
        return candidate if candidate > 0 else None

    def evaluate(self, action: PreparedTransferAction) -> MainnetTransferCode | None:
        if not self._network_enabled(action.network_id):
            return MainnetTransferCode.POLICY_UNAVAILABLE
        code = self._fee_policy(action.network_id).evaluate(action)
        if code is OfflineSigningCode.FEE_LIMIT_EXCEEDED:
            return MainnetTransferCode.FEE_LIMIT_EXCEEDED
        if code is not None:
            return MainnetTransferCode.POLICY_UNAVAILABLE
        amount_limit = self._amount_limit(action.network_id, action.asset_id)
        if amount_limit is None:
            return MainnetTransferCode.POLICY_UNAVAILABLE
        if action.amount_atomic > amount_limit:
            return MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED
        return None

    def _network_enabled(self, network_id: str) -> bool:
        if self.network_enabled is None:
            return self.enabled
        return self.network_enabled.get(network_id, False)

    def _fee_policy(self, network_id: str) -> OfflineSigningPolicy:
        if self.fee_policies is None:
            return self.fee_policy
        return self.fee_policies.get(network_id, OfflineSigningPolicy(None))

    def _amount_limit(self, network_id: str, asset_id: str) -> int | None:
        if self.amount_limits is None:
            return 2**256 - 1
        return self.amount_limits.get((network_id, asset_id))


@dataclass(frozen=True, slots=True)
class MainnetTransferResult:
    code: MainnetTransferCode
    action_id: str
    digest: str
    transaction_hash: str
    recovered_signer: str
    history_status: HistoryStatus | None
    completed_at: str
    broadcast_attempted: bool
    history_available: bool
    simulation: bool
    action_type: str = "transfer"

    @property
    def successful_submission(self) -> bool:
        return self.code in {MainnetTransferCode.PENDING, MainnetTransferCode.CONFIRMED}


@dataclass(frozen=True, slots=True)
class ReceiptTrackingResult:
    action_id: str
    transaction_hash: str
    status: HistoryStatus
    checked_at: str
    history_available: bool


class MainnetRpc(Protocol):
    def chain_id(self) -> int: ...

    def latest_block(self) -> tuple[int, int]: ...

    def native_balance(self, address: str) -> int: ...

    def token_decimals(self, contract: str) -> int: ...

    def token_balance(self, contract: str, address: str) -> int: ...

    def allowance(self, contract: str, owner: str, spender: str) -> int: ...

    def pending_nonce(self, address: str) -> int: ...

    def max_priority_fee_per_gas(self) -> int: ...

    def estimate_gas(self, transaction: Mapping[str, object]) -> int: ...

    def send_raw_transaction(self, raw_transaction: bytes) -> str: ...

    def transaction(self, transaction_hash: str) -> Mapping[str, object] | None: ...

    def transaction_receipt(
        self, transaction_hash: str,
    ) -> Mapping[str, object] | None: ...


class Web3MainnetRpc:
    """Narrow RPC surface with no account or automatic retry APIs."""

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
        return int(block["number"]), int(block["baseFeePerGas"])

    def native_balance(self, address: str) -> int:
        return int(self._call(lambda: self._web3.eth.get_balance(address)))

    def token_decimals(self, contract: str) -> int:
        token = self._web3.eth.contract(address=contract, abi=USDC_ABI)
        return int(self._call(lambda: token.functions.decimals().call()))

    def token_balance(self, contract: str, address: str) -> int:
        token = self._web3.eth.contract(address=contract, abi=USDC_ABI)
        return int(self._call(lambda: token.functions.balanceOf(address).call()))

    def allowance(self, contract: str, owner: str, spender: str) -> int:
        token = self._web3.eth.contract(address=contract, abi=USDC_ABI)
        return int(self._call(lambda: token.functions.allowance(owner, spender).call()))

    def pending_nonce(self, address: str) -> int:
        return int(
            self._call(lambda: self._web3.eth.get_transaction_count(address, "pending"))
        )

    def max_priority_fee_per_gas(self) -> int:
        return int(self._call(lambda: self._web3.eth.max_priority_fee))

    def estimate_gas(self, transaction: Mapping[str, object]) -> int:
        return int(self._call(lambda: self._web3.eth.estimate_gas(dict(transaction))))

    def send_raw_transaction(self, raw_transaction: bytes) -> str:
        return Web3.to_hex(
            self._call(lambda: self._web3.eth.send_raw_transaction(raw_transaction))
        )

    def transaction(self, transaction_hash: str) -> Mapping[str, object] | None:
        try:
            return self._call(lambda: self._web3.eth.get_transaction(transaction_hash))
        except TransactionNotFound:
            return None

    def transaction_receipt(
        self, transaction_hash: str,
    ) -> Mapping[str, object] | None:
        try:
            return self._call(
                lambda: self._web3.eth.get_transaction_receipt(transaction_hash)
            )
        except TransactionNotFound:
            return None

    @staticmethod
    def _call(call: Callable[[], object]) -> object:
        try:
            return call()
        except (TransactionNotFound, ContractLogicError, BadFunctionCallOutput):
            raise
        except (*_TRANSPORT_ERRORS, Web3Exception) as error:
            raise RuntimeError("Mainnet RPC request failed") from error


MainnetRpcFactory = Callable[[str], MainnetRpc]


class MainnetTransferExecutor:
    """Revalidates, authenticates, signs, and attempts one broadcast."""

    def __init__(
        self,
        repository: VaultRepository,
        history_store: HistoryStore,
        policy: MainnetBroadcastPolicy | None = None,
        rpc_factory: MainnetRpcFactory | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
        revoke_policy: RevokePolicy | None = None,
    ) -> None:
        self.repository = repository
        self.history_store = history_store
        self.policy = policy or MainnetBroadcastPolicy.from_environment(environ)
        self._rpc_factory = rpc_factory or (lambda endpoint: Web3MainnetRpc(endpoint))
        self._environ = os.environ if environ is None else environ
        self._clock = clock or (lambda: datetime.now(UTC))
        self.revoke_policy = revoke_policy or RevokePolicy.from_environment(
            self._environ,
        )

    def execute(
        self,
        action: PreparedTransactionAction,
        expected_digest: str,
        password: str,
        permit: SigningPermit,
    ) -> MainnetTransferResult:
        now = self._clock().astimezone(UTC)
        validation = validate_signing_action(action, expected_digest, now)
        if validation is OfflineSigningCode.ACTION_EXPIRED:
            return self._failure(action, MainnetTransferCode.ACTION_EXPIRED)
        if validation is not None:
            return self._failure(action, MainnetTransferCode.ACTION_INVALID)
        policy_code = _evaluate_policy(self.policy, self.revoke_policy, action)
        if policy_code is not None:
            return self._failure(action, policy_code)
        if permit.cancelled:
            return self._failure(action, MainnetTransferCode.CANCELLED)

        endpoint = _endpoint(self._environ, action.network_id)
        if endpoint is None:
            return self._failure(action, MainnetTransferCode.POLICY_UNAVAILABLE)
        try:
            rpc = self._rpc_factory(endpoint)
            if not _final_revalidation(rpc, action):
                return self._failure(action, MainnetTransferCode.REVALIDATION_FAILED)
        except Exception:
            return self._failure(action, MainnetTransferCode.REVALIDATION_FAILED)
        if permit.cancelled:
            return self._failure(action, MainnetTransferCode.CANCELLED)
        if self._clock().astimezone(UTC) >= action.expires_at:
            return self._failure(action, MainnetTransferCode.ACTION_EXPIRED)

        private_key: bytearray | None = None
        signed = None
        decoded = None
        transaction_hash = ""
        recovered = ""
        history_status: HistoryStatus | None = None
        broadcast_attempted = False
        try:
            record = self.repository._authenticate_profile(password, action.profile_id)
            if permit.cancelled:
                return self._failure(action, MainnetTransferCode.CANCELLED)
            if self._clock().astimezone(UTC) >= action.expires_at:
                return self._failure(action, MainnetTransferCode.ACTION_EXPIRED)
            if (
                record.summary.profile_id != action.profile_id
                or not hmac.compare_digest(
                    record.summary.address.lower(), action.sender.lower(),
                )
                or not hmac.compare_digest(
                    rederive(record.secret).lower(), action.sender.lower(),
                )
            ):
                return self._failure(action, MainnetTransferCode.ACTION_INVALID)

            private_key = bytearray(private_key_bytes(record.secret))
            signed = Account.sign_transaction(transaction_dict(action), bytes(private_key))
            recovered = Web3.to_checksum_address(
                Account.recover_transaction(signed.raw_transaction)
            )
            decoded = TypedTransaction.from_bytes(
                HexBytes(signed.raw_transaction)
            ).as_dict()
            if (
                permit.cancelled
                or recovered.lower() != action.sender.lower()
                or not decoded_transaction_matches(decoded, action)
            ):
                return self._failure(
                    action,
                    MainnetTransferCode.CANCELLED
                    if permit.cancelled else MainnetTransferCode.SIGNING_FAILED,
                )
            transaction_hash = Web3.to_hex(signed.hash)
            try:
                self.history_store.update_status(
                    action.action_id,
                    HistoryStatus.UNKNOWN,
                    _timestamp(self._clock()),
                    transaction_hash,
                )
                history_status = HistoryStatus.UNKNOWN
            except (HistoryUnavailableError, HistoryValidationError, StorageError):
                return self._failure(action, MainnetTransferCode.HISTORY_UNAVAILABLE)
            if permit.cancelled:
                return self._result(
                    action,
                    MainnetTransferCode.CANCELLED,
                    transaction_hash,
                    recovered,
                    history_status,
                    False,
                )

            broadcast_attempted = True
            try:
                remote_hash = rpc.send_raw_transaction(signed.raw_transaction)
            except Exception:
                return self._result(
                    action,
                    MainnetTransferCode.UNKNOWN,
                    transaction_hash,
                    recovered,
                    history_status,
                    broadcast_attempted,
                )
            if not hmac.compare_digest(remote_hash.lower(), transaction_hash.lower()):
                return self._result(
                    action,
                    MainnetTransferCode.UNKNOWN,
                    transaction_hash,
                    recovered,
                    history_status,
                    broadcast_attempted,
                )
            try:
                self.history_store.update_status(
                    action.action_id,
                    HistoryStatus.PENDING,
                    _timestamp(self._clock()),
                    transaction_hash,
                )
                history_status = HistoryStatus.PENDING
                code = MainnetTransferCode.PENDING
                history_available = True
            except (HistoryUnavailableError, HistoryValidationError, StorageError):
                code = MainnetTransferCode.UNKNOWN
                history_available = False
            return self._result(
                action,
                code,
                transaction_hash,
                recovered,
                history_status,
                broadcast_attempted,
                history_available,
            )
        except (AuthenticationFailedError, VaultUnavailableError, InvalidSecretError):
            return self._failure(action, MainnetTransferCode.AUTHENTICATION_FAILED)
        except Exception:
            return self._result(
                action,
                MainnetTransferCode.SIGNING_FAILED,
                transaction_hash,
                recovered,
                history_status,
                broadcast_attempted,
            )
        finally:
            if private_key is not None:
                for index in range(len(private_key)):
                    private_key[index] = 0
            del private_key, signed, decoded, password

    def _failure(
        self, action: PreparedTransactionAction, code: MainnetTransferCode,
    ) -> MainnetTransferResult:
        return self._result(action, code, "", "", None, False)

    def _result(
        self,
        action: PreparedTransactionAction,
        code: MainnetTransferCode,
        transaction_hash: str,
        recovered_signer: str,
        history_status: HistoryStatus | None,
        broadcast_attempted: bool,
        history_available: bool = True,
    ) -> MainnetTransferResult:
        return MainnetTransferResult(
            code,
            action.action_id,
            action.digest,
            transaction_hash,
            recovered_signer,
            history_status,
            _timestamp(self._clock()),
            broadcast_attempted,
            history_available,
            action.simulation,
            REVOKE_ACTION_TYPE if isinstance(action, PreparedRevokeAction) else "transfer",
        )


class BroadcastReceiptTracker:
    """Checks public transaction state without signing or rebroadcasting."""

    def __init__(
        self,
        history_store: HistoryStore,
        rpc_factory: MainnetRpcFactory | None = None,
        environ: Mapping[str, str] | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] | None = None,
        sleeper: Callable[[float], None] | None = None,
        timeout_seconds: float = 120.0,
        poll_interval_seconds: float = 3.0,
    ) -> None:
        self.history_store = history_store
        self._rpc_factory = rpc_factory or (lambda endpoint: Web3MainnetRpc(endpoint))
        self._environ = os.environ if environ is None else environ
        self._clock = clock or (lambda: datetime.now(UTC))
        self._monotonic = monotonic or time.monotonic
        self._sleeper = sleeper or time.sleep
        self.timeout_seconds = max(0.0, float(timeout_seconds))
        self.poll_interval_seconds = max(0.01, float(poll_interval_seconds))

    def track(
        self, action_id: str, cancelled: Event | None = None,
    ) -> ReceiptTrackingResult:
        deadline = self._monotonic() + self.timeout_seconds
        result = self.check_once(action_id)
        while (
            result.status not in {HistoryStatus.CONFIRMED, HistoryStatus.FAILED}
            and self._monotonic() < deadline
            and not (cancelled is not None and cancelled.is_set())
        ):
            self._sleeper(
                min(self.poll_interval_seconds, max(0.0, deadline - self._monotonic()))
            )
            if cancelled is not None and cancelled.is_set():
                break
            result = self.check_once(action_id)
        return result

    def check_once(self, action_id: str) -> ReceiptTrackingResult:
        records = self.history_store.load()
        record = next((item for item in records if item.action_id == action_id), None)
        if record is None or record.transaction_hash is None:
            raise HistoryValidationError("History action cannot be checked")
        if record.status in {HistoryStatus.CONFIRMED, HistoryStatus.FAILED}:
            return self._result(record, record.status, True)
        endpoint = _endpoint(self._environ, record.network)
        if endpoint is None:
            return self._result(record, record.status, True)
        observed = record.status
        actual_fee_wei: str | None = None
        try:
            rpc = self._rpc_factory(endpoint)
            if rpc.chain_id() != record.chain_id:
                raise RuntimeError("Receipt RPC chain mismatch")
            receipt = rpc.transaction_receipt(record.transaction_hash)
            if receipt is not None:
                transaction = (
                    rpc.transaction(record.transaction_hash)
                    if record.token == "ETH" or record.action_type == REVOKE_ACTION_TYPE
                    else None
                )
                observed = _receipt_status(receipt, record, transaction)
                if observed in {HistoryStatus.CONFIRMED, HistoryStatus.FAILED}:
                    actual_fee_wei = _receipt_fee_wei(receipt)
            elif record.status is HistoryStatus.PENDING:
                observed = HistoryStatus.PENDING
            else:
                transaction = rpc.transaction(record.transaction_hash)
                observed = (
                    HistoryStatus.PENDING
                    if transaction is not None
                    and _public_transaction_matches(transaction, record)
                    else HistoryStatus.UNKNOWN
                )
        except Exception:
            observed = (
                HistoryStatus.PENDING
                if record.status is HistoryStatus.PENDING
                else HistoryStatus.UNKNOWN
            )
        if observed is record.status and (
            actual_fee_wei is None or record.actual_fee_wei is not None
        ):
            return self._result(record, observed, True)
        try:
            updated = self.history_store.update_status(
                record.action_id,
                observed,
                _timestamp(self._clock()),
                record.transaction_hash,
                actual_fee_wei,
            )
            current = next(item for item in updated if item.action_id == record.action_id)
            return self._result(current, observed, True)
        except (HistoryUnavailableError, HistoryValidationError, StorageError):
            return self._result(record, observed, False)

    def _result(
        self,
        record: WalletHistoryRecord,
        status: HistoryStatus,
        history_available: bool,
    ) -> ReceiptTrackingResult:
        return ReceiptTrackingResult(
            record.action_id,
            record.transaction_hash or "",
            status,
            _timestamp(self._clock()),
            history_available,
        )


def mainnet_result_to_map(result: MainnetTransferResult) -> dict[str, object]:
    title, message = _result_text(result.code, result.action_type)
    status = result.history_status.value if result.history_status is not None else ""
    return {
        "code": result.code.value,
        "title": title,
        "message": message,
        "actionId": result.action_id,
        "digest": result.digest,
        "shortDigest": _short_hash(result.digest),
        "transactionHash": result.transaction_hash,
        "shortTransactionHash": _short_hash(result.transaction_hash),
        "recoveredSigner": result.recovered_signer,
        "shortRecoveredSigner": _short_address(result.recovered_signer),
        "historyStatus": status,
        "statusLabel": status.capitalize(),
        "completedAt": result.completed_at,
        "broadcastAttempted": result.broadcast_attempted,
        "historyAvailable": result.history_available,
        "canCheckStatus": bool(result.transaction_hash)
        and result.history_status in {HistoryStatus.PENDING, HistoryStatus.UNKNOWN},
        "confirmed": result.code is MainnetTransferCode.CONFIRMED,
        "submitted": result.successful_submission,
        "simulation": result.simulation,
        "actionType": result.action_type,
    }


def result_from_tracking(
    previous: MainnetTransferResult,
    tracking: ReceiptTrackingResult,
) -> MainnetTransferResult:
    code = {
        HistoryStatus.CONFIRMED: MainnetTransferCode.CONFIRMED,
        HistoryStatus.FAILED: MainnetTransferCode.FAILED,
        HistoryStatus.PENDING: MainnetTransferCode.PENDING,
        HistoryStatus.UNKNOWN: MainnetTransferCode.UNKNOWN,
    }[tracking.status]
    return MainnetTransferResult(
        code,
        previous.action_id,
        previous.digest,
        previous.transaction_hash,
        previous.recovered_signer,
        tracking.status,
        tracking.checked_at,
        previous.broadcast_attempted,
        previous.history_available and tracking.history_available,
        previous.simulation,
        previous.action_type,
    )


def _evaluate_policy(
    transfer_policy: MainnetBroadcastPolicy,
    revoke_policy: RevokePolicy,
    action: PreparedTransactionAction,
) -> MainnetTransferCode | None:
    if not isinstance(action, PreparedRevokeAction):
        return transfer_policy.evaluate(action)
    code = revoke_policy.evaluate(action)
    if code is RevokePolicyCode.FEE_LIMIT_EXCEEDED:
        return MainnetTransferCode.FEE_LIMIT_EXCEEDED
    if code is not None:
        return MainnetTransferCode.POLICY_UNAVAILABLE
    return None


def _final_revalidation(rpc: MainnetRpc, action: PreparedTransactionAction) -> bool:
    if isinstance(action, PreparedRevokeAction):
        return _final_revoke_revalidation(rpc, action)
    tx = action.transaction
    route = transfer_route(action.network_id, action.asset_id)
    if rpc.chain_id() != route.chain_id:
        return False
    block_number, base_fee = rpc.latest_block()
    native_balance = int(rpc.native_balance(action.sender))
    token_balance: int | None = None
    if route.token_contract is not None:
        decimals = int(rpc.token_decimals(route.token_contract))
        if decimals != route.decimals:
            return False
        token_balance = int(rpc.token_balance(route.token_contract, action.sender))
    nonce = int(rpc.pending_nonce(action.sender))
    priority_fee = int(rpc.max_priority_fee_per_gas())
    estimate = int(
        rpc.estimate_gas(
            {
                "from": action.sender,
                "to": tx.to,
                "value": tx.value,
                "data": tx.data,
                "nonce": tx.nonce,
                "type": tx.transaction_type,
                "chainId": tx.chain_id,
                "maxFeePerGas": tx.max_fee_per_gas,
                "maxPriorityFeePerGas": tx.max_priority_fee_per_gas,
            }
        )
    )
    current_required_fee = 2 * int(base_fee) + priority_fee
    required_native = action.max_total_fee_wei + (
        action.amount_atomic if route.token_contract is None else 0
    )
    return (
        block_number >= action.block_number
        and base_fee > 0
        and (token_balance is None or token_balance >= action.amount_atomic)
        and native_balance >= required_native
        and nonce == tx.nonce
        and 0 < estimate <= tx.gas
        and 0 <= priority_fee <= tx.max_priority_fee_per_gas
        and 0 < current_required_fee <= tx.max_fee_per_gas
    )


def _final_revoke_revalidation(
    rpc: MainnetRpc, action: PreparedRevokeAction,
) -> bool:
    tx = action.transaction
    try:
        route = approval_route(action.network_id)
        if rpc.chain_id() != route.chain_id:
            return False
        block_number, base_fee = rpc.latest_block()
        decimals = int(rpc.token_decimals(route.token_contract))
        allowance = int(
            rpc.allowance(route.token_contract, action.sender, action.spender),
        )
        native_balance = int(rpc.native_balance(action.sender))
        nonce = int(rpc.pending_nonce(action.sender))
        priority_fee = int(rpc.max_priority_fee_per_gas())
        estimate = int(rpc.estimate_gas({
            "from": action.sender,
            "to": tx.to,
            "value": tx.value,
            "data": tx.data,
            "nonce": tx.nonce,
            "type": tx.transaction_type,
            "chainId": tx.chain_id,
            "maxFeePerGas": tx.max_fee_per_gas,
            "maxPriorityFeePerGas": tx.max_priority_fee_per_gas,
        }))
    except Exception:
        return False
    current_required_fee = 2 * int(base_fee) + priority_fee
    return (
        block_number >= action.block_number
        and int(base_fee) > 0
        and decimals == action.decimals == 6
        and allowance == action.allowance_before_atomic
        and allowance > 0
        and native_balance >= action.max_total_fee_wei
        and nonce == tx.nonce
        and 0 < estimate <= tx.gas
        and 0 <= priority_fee <= tx.max_priority_fee_per_gas
        and 0 < current_required_fee <= tx.max_fee_per_gas
        and tx.to.lower() == route.token_contract.lower()
        and tx.value == 0
        and tx.data == encode_usdc_approve_zero(action.spender)
    )


def _receipt_status(
    receipt: Mapping[str, object],
    record: WalletHistoryRecord,
    transaction: Mapping[str, object] | None = None,
) -> HistoryStatus:
    try:
        receipt_hash = _hex_value(receipt["transactionHash"])
        if receipt_hash.lower() != (record.transaction_hash or "").lower():
            return HistoryStatus.UNKNOWN
        sender = str(receipt.get("from", record.sender))
        if record.token == "USDC" and record.contract is None:
            return HistoryStatus.UNKNOWN
        expected_target = record.recipient if record.token == "ETH" else record.contract
        target = str(receipt.get("to", expected_target))
        if sender.lower() != record.sender.lower():
            return HistoryStatus.UNKNOWN
        if target.lower() != expected_target.lower():
            return HistoryStatus.UNKNOWN
        if _receipt_fee_wei(receipt) is None:
            return HistoryStatus.UNKNOWN
        status = int(receipt["status"])
        if status == 0:
            return HistoryStatus.FAILED
        if status != 1:
            return HistoryStatus.UNKNOWN
        if record.token == "ETH" or record.action_type == REVOKE_ACTION_TYPE:
            if transaction is None or not _public_transaction_matches(transaction, record):
                return HistoryStatus.UNKNOWN
        if record.token == "ETH":
            return (
                HistoryStatus.CONFIRMED
                if transaction is not None
                else HistoryStatus.UNKNOWN
            )
        logs = receipt["logs"]
        if not isinstance(logs, (list, tuple)):
            return HistoryStatus.UNKNOWN
        matcher = (
            _matching_approval_log
            if record.action_type == REVOKE_ACTION_TYPE
            else _matching_transfer_log
        )
        return (
            HistoryStatus.CONFIRMED
            if any(matcher(item, record) for item in logs)
            else HistoryStatus.UNKNOWN
        )
    except (KeyError, TypeError, ValueError):
        return HistoryStatus.UNKNOWN


def _matching_transfer_log(value: object, record: WalletHistoryRecord) -> bool:
    if not isinstance(value, Mapping) or record.contract is None:
        return False
    try:
        if str(value["address"]).lower() != record.contract.lower():
            return False
        topics = value["topics"]
        if not isinstance(topics, (list, tuple)) or len(topics) < 3:
            return False
        rendered = [_hex_value(topic).lower() for topic in topics[:3]]
        sender_topic = "0x" + record.sender[2:].lower().rjust(64, "0")
        recipient_topic = "0x" + record.recipient[2:].lower().rjust(64, "0")
        amount = int.from_bytes(HexBytes(value["data"]), "big")
        return (
            rendered[0] == TRANSFER_EVENT_TOPIC.lower()
            and rendered[1] == sender_topic
            and rendered[2] == recipient_topic
            and amount == int(record.amount_atomic)
        )
    except (KeyError, TypeError, ValueError):
        return False


def _matching_approval_log(value: object, record: WalletHistoryRecord) -> bool:
    if not isinstance(value, Mapping) or record.contract is None:
        return False
    try:
        if str(value["address"]).lower() != record.contract.lower():
            return False
        topics = value["topics"]
        if not isinstance(topics, (list, tuple)) or len(topics) != 3:
            return False
        rendered = [_hex_value(topic).lower() for topic in topics[:3]]
        owner_topic = "0x" + record.sender[2:].lower().rjust(64, "0")
        spender_topic = "0x" + record.recipient[2:].lower().rjust(64, "0")
        encoded_amount = HexBytes(value["data"])
        if len(encoded_amount) != 32:
            return False
        amount = int.from_bytes(encoded_amount, "big")
        return (
            rendered[0] == APPROVAL_EVENT_TOPIC.lower()
            and rendered[1] == owner_topic
            and rendered[2] == spender_topic
            and amount == 0
        )
    except (KeyError, TypeError, ValueError):
        return False


def _receipt_fee_wei(receipt: Mapping[str, object]) -> str | None:
    try:
        gas_used = int(receipt["gasUsed"])
        effective_gas_price = int(receipt["effectiveGasPrice"])
        if gas_used < 0 or effective_gas_price < 0:
            return None
        return str(gas_used * effective_gas_price)
    except (KeyError, TypeError, ValueError):
        return None


def _public_transaction_matches(
    value: Mapping[str, object], record: WalletHistoryRecord,
) -> bool:
    try:
        transaction_hash = _hex_value(value["hash"])
        sender = str(value["from"])
        target = str(value["to"])
        if record.token == "USDC" and record.contract is None:
            return False
        expected_target = record.recipient if record.token == "ETH" else record.contract
        transaction_value = int(value.get("value", 0))
        data = _transaction_data(value)
        if record.action_type == REVOKE_ACTION_TYPE:
            expected_data = encode_usdc_approve_zero(record.recipient)
            expected_value = 0
        elif record.token == "ETH":
            expected_data = "0x"
            expected_value = int(record.amount_atomic)
        else:
            expected_data = encode_usdc_transfer(
                record.recipient, int(record.amount_atomic),
            )
            expected_value = 0
        return (
            transaction_hash.lower() == (record.transaction_hash or "").lower()
            and sender.lower() == record.sender.lower()
            and target.lower() == expected_target.lower()
            and transaction_value == expected_value
            and data.lower() == expected_data.lower()
            and int(value["chainId"]) == record.chain_id
        )
    except (KeyError, TypeError, ValueError):
        return False


def _endpoint(environ: Mapping[str, str], network_id: str = BASE_NETWORK_ID) -> str | None:
    try:
        route: TransferRouteSpec = transfer_route(network_id, ETH_ASSET_ID)
    except Exception:
        return None
    value = environ.get(route.endpoint_env, route.default_endpoint).strip()
    return value or None


def _transaction_data(value: Mapping[str, object]) -> str:
    candidate = value.get("input", value.get("data", "0x"))
    return _hex_value(candidate)


def _positive_environment_value(
    environ: Mapping[str, str], name: str,
) -> int | None:
    value = environ.get(name, "").strip()
    if not value or not value.isascii() or not value.isdecimal() or value.startswith("0"):
        return None
    parsed = int(value)
    return parsed if 0 < parsed < 2**256 else None


def _hex_value(value: object) -> str:
    if isinstance(value, str):
        if value.startswith("0x"):
            return value
        raise ValueError("Hex value is invalid")
    return Web3.to_hex(value)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _short_hash(value: str) -> str:
    return f"{value[:14]}…{value[-10:]}" if value else ""


def _short_address(value: str) -> str:
    return f"{value[:8]}…{value[-6:]}" if value else ""


def _result_text(
    code: MainnetTransferCode, action_type: str = "transfer",
) -> tuple[str, str]:
    values = {
        MainnetTransferCode.CONFIRMED: (
            "Transfer confirmed",
            "The exact reviewed transfer was confirmed on-chain.",
        ),
        MainnetTransferCode.PENDING: (
            "Transaction submitted",
            "Broadcast occurred once. Confirmation is still pending.",
        ),
        MainnetTransferCode.UNKNOWN: (
            "Submission status unknown",
            "The transaction will not be sent again. Check its public hash safely.",
        ),
        MainnetTransferCode.FAILED: (
            "Transaction reverted",
            "A network fee may have been spent, but the transfer reverted.",
        ),
        MainnetTransferCode.AUTHENTICATION_FAILED: (
            "Authentication failed",
            "Nothing was sent. Prepare a new action to try again.",
        ),
        MainnetTransferCode.POLICY_UNAVAILABLE: (
            "Mainnet sending disabled",
            "The route requires local broadcast, amount, and fee limits.",
        ),
        MainnetTransferCode.FEE_LIMIT_EXCEEDED: (
            "Fee limit exceeded",
            "Nothing was sent. Prepare a new action when fees are lower.",
        ),
        MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED: (
            "Amount limit exceeded",
            "Nothing was sent. The transfer exceeds the local route limit.",
        ),
        MainnetTransferCode.ACTION_INVALID: (
            "Transaction changed",
            "Nothing was sent. The reviewed action is no longer valid.",
        ),
        MainnetTransferCode.ACTION_EXPIRED: (
            "Preparation expired",
            "Nothing was sent. Live transaction data must be prepared again.",
        ),
        MainnetTransferCode.REVALIDATION_FAILED: (
            "Live revalidation failed",
            "Nothing was sent. Network data changed or became unavailable.",
        ),
        MainnetTransferCode.HISTORY_UNAVAILABLE: (
            "History unavailable",
            "Nothing was sent because the public transaction hash could not be saved.",
        ),
        MainnetTransferCode.CANCELLED: (
            "Transfer cancelled",
            "No automatic retry or broadcast will occur.",
        ),
        MainnetTransferCode.SIGNING_FAILED: (
            "Signing failed",
            "Nothing was sent. Prepare a new action to try again.",
        ),
    }
    title, message = values[code]
    if action_type != REVOKE_ACTION_TYPE:
        return title, message
    replacements = {
        MainnetTransferCode.CONFIRMED: (
            "Approval revoked",
            "The exact reviewed USDC allowance was set to zero on-chain.",
        ),
        MainnetTransferCode.FAILED: (
            "Revoke reverted",
            "A network fee may have been spent, but the allowance was not revoked.",
        ),
        MainnetTransferCode.POLICY_UNAVAILABLE: (
            "Revoke unavailable",
            "The selected route requires its local spender, enable, and fee settings.",
        ),
        MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED: (
            "Revoke unavailable",
            "The revoke action does not use a transfer amount limit.",
        ),
        MainnetTransferCode.CANCELLED: (
            "Revoke cancelled",
            "No automatic retry or broadcast will occur.",
        ),
    }
    return replacements.get(code, (title, message))


_TRANSPORT_ERRORS = (
    request_errors.ConnectionError,
    request_errors.Timeout,
    request_errors.HTTPError,
    TimeoutError,
)
