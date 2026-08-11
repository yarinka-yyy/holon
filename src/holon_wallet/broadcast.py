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
    Web3RPCError,
)
from holon_contracts import RefusalCode
from holon_policy import (
    Policy, PolicyEngine, PolicyRevisionStore, PolicyRevisionUnavailable,
    PolicySnapshot, policy_digest,
)
from holon_lending import ActionProfilesState
from holon_lending.preflight import (
    BASE_GAS_PRICE_ORACLE, MAX_UINT256, Web3AavePreflightRpc, encode_approve,
    encode_compound_supply, encode_compound_withdraw, encode_morpho_deposit,
    encode_morpho_redeem, encode_morpho_withdraw, encode_supply, encode_withdraw,
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
from .public_data import NETWORK_BY_ID, USDC_ABI
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
    TRANSFER_ROUTES,
    PreparedTransferAction,
    SigningPermit,
    encode_usdc_transfer,
    transfer_route,
)
from .vault import AuthenticationFailedError, VaultRepository, VaultUnavailableError
from .wallet_crypto import InvalidSecretError, private_key_bytes, rederive

BROADCAST_ENABLED_ENV = "HOLON_BASE_BROADCAST_ENABLED"
BASE_RPC_ENV = "HOLON_BASE_RPC_URL"
DEFAULT_BASE_RPC_URL = "https://mainnet.base.org"
ALCHEMY_BASE_RPC_URL = "https://base-mainnet.g.alchemy.com/public"
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
AAVE_SUPPLY_EVENT_TOPIC = Web3.to_hex(
    Web3.keccak(text="Supply(address,address,address,uint256,uint16)"),
)
PreparedTransactionAction = PreparedTransferAction | PreparedRevokeAction


class MainnetTransferCode(str, Enum):
    CONFIRMED = "CONFIRMED"
    PENDING = "PENDING"
    UNKNOWN = "UNKNOWN"
    SUBMISSION_REJECTED = "SUBMISSION_REJECTED"
    RECEIPT_RPC_UNAVAILABLE = "RECEIPT_RPC_UNAVAILABLE"
    RECEIPT_WRONG_CHAIN = "RECEIPT_WRONG_CHAIN"
    RECEIPT_VALIDATION_FAILED = "RECEIPT_VALIDATION_FAILED"
    FAILED = "FAILED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    POLICY_AUTHORITY_DISABLED = "POLICY_AUTHORITY_DISABLED"
    POLICY_VERSION_MISMATCH = "POLICY_VERSION_MISMATCH"
    NETWORK_NOT_ALLOWED = "NETWORK_NOT_ALLOWED"
    ASSET_NOT_ALLOWED = "ASSET_NOT_ALLOWED"
    RECIPIENT_NOT_ALLOWED = "RECIPIENT_NOT_ALLOWED"
    FEE_LIMIT_EXCEEDED = "FEE_LIMIT_EXCEEDED"
    AMOUNT_LIMIT_EXCEEDED = "AMOUNT_LIMIT_EXCEEDED"
    ACTION_INVALID = "ACTION_INVALID"
    ACTION_EXPIRED = "ACTION_EXPIRED"
    REVALIDATION_FAILED = "REVALIDATION_FAILED"
    REVALIDATION_RPC_UNAVAILABLE = "REVALIDATION_RPC_UNAVAILABLE"
    HISTORY_UNAVAILABLE = "HISTORY_UNAVAILABLE"
    CANCELLED = "CANCELLED"
    SIGNING_FAILED = "SIGNING_FAILED"


class ReceiptTrackingCode(str, Enum):
    PENDING = "RECEIPT_PENDING"
    CONFIRMED = "RECEIPT_CONFIRMED"
    FAILED = "RECEIPT_FAILED"
    TRANSACTION_UNKNOWN = "RECEIPT_TRANSACTION_UNKNOWN"
    RPC_UNAVAILABLE = "RECEIPT_RPC_UNAVAILABLE"
    WRONG_CHAIN = "RECEIPT_WRONG_CHAIN"
    VALIDATION_FAILED = "RECEIPT_VALIDATION_FAILED"


class SubmissionRejectedError(RuntimeError):
    """A provider returned a definite JSON-RPC rejection for one submission."""


class SubmissionUnknownError(RuntimeError):
    """A provider transport failure leaves a submission outcome ambiguous."""


@dataclass(slots=True)
class MainnetBroadcastPolicy:
    enabled: bool
    fee_policy: OfflineSigningPolicy
    network_enabled: Mapping[str, bool] | None = None
    fee_policies: Mapping[str, OfflineSigningPolicy] | None = None
    amount_limits: Mapping[tuple[str, str], int | None] | None = None
    shared_engine: PolicyEngine | None = None
    load_failure: str | None = None
    policy_revision: int = 0
    policy_digest_value: str = ""
    revision_store: PolicyRevisionStore | None = None

    @classmethod
    def from_policy(cls, policy: Policy) -> MainnetBroadcastPolicy:
        return cls(
            False,
            OfflineSigningPolicy(None),
            shared_engine=PolicyEngine(policy),
            policy_digest_value=policy_digest(policy.to_dict()),
        )

    @classmethod
    def from_snapshot(
        cls, snapshot: PolicySnapshot,
        store: PolicyRevisionStore | None = None,
    ) -> MainnetBroadcastPolicy:
        return cls(
            False, OfflineSigningPolicy(None),
            shared_engine=PolicyEngine(snapshot.policy),
            policy_revision=snapshot.policy_revision,
            policy_digest_value=snapshot.policy_digest,
            revision_store=store,
        )

    @classmethod
    def unavailable(cls, code: str = "POLICY_UNAVAILABLE") -> MainnetBroadcastPolicy:
        return cls(False, OfflineSigningPolicy(None), load_failure=code)

    def refresh(self) -> bool:
        if self.revision_store is None:
            return self.load_failure is None
        try:
            snapshot = self.revision_store.load()
        except PolicyRevisionUnavailable:
            self.load_failure = "POLICY_STATE_INVALID"
            return False
        self.shared_engine = PolicyEngine(snapshot.policy)
        self.policy_revision = snapshot.policy_revision
        self.policy_digest_value = snapshot.policy_digest
        self.load_failure = None
        return True

    def matches(self, revision: int, digest: str) -> bool:
        if self.revision_store is None and digest == "":
            return True
        return (
            self.refresh()
            and self.policy_revision == revision
            and self.policy_digest_value == digest
        )

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
        if self.shared_engine is not None:
            return (
                self.load_failure is None
                and self.shared_engine.policy.authority_enabled
                and bool(self.shared_engine.policy.transfer_rules)
            )
        return (
            self._network_enabled(BASE_NETWORK_ID)
            and self._fee_policy(BASE_NETWORK_ID).available
            and self._amount_limit(BASE_NETWORK_ID, "usdc") is not None
        )

    @property
    def display(self) -> str:
        return self.fee_policy.display

    def display_for(self, action: PreparedTransferAction) -> str:
        if self.shared_engine is not None:
            if action.action_type == "lending":
                rule = next(iter(self.shared_engine.policy.lending_rules), None)
                return (
                    OfflineSigningPolicy(int(rule.max_total_fee_wei)).display
                    if rule is not None
                    else "No fixed cap"
                )
            rule = self._shared_rule(action.network_id, action.asset_id)
            return (
                OfflineSigningPolicy(int(rule.max_total_fee_wei)).display
                if rule is not None else "Not configured"
            )
        return self._fee_policy(action.network_id).display

    def amount_display_for(self, action: PreparedTransferAction) -> str:
        if self.shared_engine is not None:
            if action.action_type == "lending":
                rule = next(iter(self.shared_engine.policy.lending_rules), None)
                return str(rule.max_amount_atomic) if rule is not None else "Exact action amount"
            rule = self._shared_rule(action.network_id, action.asset_id)
            recipient = self._shared_recipient_limit(rule, action.recipient)
            if rule is None or recipient is None:
                return "Not configured"
            return str(min(int(rule.max_amount_atomic), recipient))
        limit = self._amount_limit(action.network_id, action.asset_id)
        if limit is None:
            return "Not configured"
        return str(limit)

    def draft_amount_code(
        self, network_id: str, asset_id: str, amount_atomic: int,
        recipient: str | None = None,
        policy_version: str | None = None,
    ) -> MainnetTransferCode | None:
        if not self.refresh():
            return MainnetTransferCode.POLICY_UNAVAILABLE
        if self.shared_engine is not None:
            if (
                policy_version is not None
                and policy_version != self.shared_engine.policy.policy_version
            ):
                return MainnetTransferCode.POLICY_VERSION_MISMATCH
            decision, _ = self.shared_engine.evaluate_intent(
                network_id, asset_id, amount_atomic, recipient,
            )
            return self._shared_code(decision.code) if not decision.allowed else None
        if self.load_failure is not None:
            return MainnetTransferCode.POLICY_UNAVAILABLE
        limit = self._amount_limit(network_id, asset_id)
        if limit is not None and amount_atomic > limit:
            return MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED
        return None

    def lending_intent_code(
        self, action: str, amount_mode: str, amount_atomic: int | None,
        action_profile_digest: str,
        policy_version: str | None = None,
        protocol_profile_id: str = "aave-v3-base-usdc",
    ) -> MainnetTransferCode | None:
        """Reject a semantic Lending intent before Wallet performs any RPC."""
        if not self.refresh() or self.shared_engine is None:
            return MainnetTransferCode.POLICY_UNAVAILABLE
        if (
            policy_version is not None
            and policy_version != self.shared_engine.policy.policy_version
        ):
            return MainnetTransferCode.POLICY_VERSION_MISMATCH
        decision, _rule = self.shared_engine.evaluate_lending_intent({
            "module_id": "lending", "protocol_profile_id": protocol_profile_id,
            "network": "base", "asset": "usdc", "action": action,
            "amount_mode": amount_mode, "amount_atomic": amount_atomic,
        }, action_profile_digest)
        return self._shared_code(decision.code) if not decision.allowed else None

    def maximum_draft_amount(
        self,
        network_id: str,
        asset_id: str,
        available_atomic: int,
        recipient: str | None = None,
    ) -> int | None:
        if not self.refresh():
            return None
        if type(available_atomic) is not int or available_atomic <= 0:
            return None
        candidate = available_atomic
        if self.shared_engine is not None:
            decision, rule = self.shared_engine.evaluate_intent(
                network_id, asset_id, 1, recipient,
            )
            if not decision.allowed or rule is None:
                return None
            recipient_limit = self._shared_recipient_limit(rule, recipient)
            if recipient_limit is None:
                return None
            return min(candidate, int(rule.max_amount_atomic), recipient_limit)
        if self.load_failure is not None:
            return None
        amount_limit = self._amount_limit(network_id, asset_id)
        if amount_limit is not None:
            candidate = min(candidate, amount_limit)
        return candidate if candidate > 0 else None

    def evaluate(self, action: PreparedTransferAction) -> MainnetTransferCode | None:
        if not self.matches(action.policy_revision, action.policy_digest):
            return MainnetTransferCode.REVALIDATION_FAILED
        if self.shared_engine is not None:
            if action.action_type == "lending":
                requested_action = (
                    "withdraw" if action.method in {"withdraw", "redeem"} else "supply"
                )
                selected = ActionProfilesState.load().select_by_digest(
                    action.action_profile_digest,
                )
                if selected is None:
                    return MainnetTransferCode.POLICY_UNAVAILABLE
                decision, rule = self.shared_engine.evaluate_lending_intent({
                    "module_id": "lending", "protocol_profile_id": selected.profile_id,
                    "network": "base", "asset": "usdc", "action": requested_action,
                    "amount_mode": action.amount_mode,
                    "amount_atomic": action.amount_atomic,
                }, action.action_profile_digest)
                if decision.allowed:
                    decision = self.shared_engine.evaluate_lending_prepared(
                        action.method, action.amount_atomic, action.max_total_fee_wei, rule,
                    )
                return self._shared_code(decision.code) if not decision.allowed else None
            decision = self.shared_engine.evaluate_transfer({
                "policy_version": self.shared_engine.policy.policy_version,
                "action_type": "transfer",
                "network": action.network_id,
                "asset": action.asset_id,
                "amount_atomic": str(action.amount_atomic),
                "recipient": action.recipient,
                "max_total_fee_wei": str(action.max_total_fee_wei),
            })
            return self._shared_code(decision.code) if not decision.allowed else None
        if self.load_failure is not None:
            return MainnetTransferCode.POLICY_UNAVAILABLE
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

    def _shared_rule(self, network_id: str, asset_id: str):
        if self.shared_engine is None:
            return None
        return next((
            rule for rule in self.shared_engine.policy.transfer_rules
            if rule.network == network_id and rule.asset == asset_id
        ), None)

    @staticmethod
    def _shared_recipient_limit(rule, recipient: str | None) -> int | None:
        if rule is None or not isinstance(recipient, str):
            return None
        normalized = recipient.lower()
        item = next((entry for entry in rule.recipients if entry.address == normalized), None)
        return int(item.max_amount_atomic) if item is not None else None

    @staticmethod
    def _shared_code(code: str) -> MainnetTransferCode:
        mapping = {
            RefusalCode.POLICY_AUTHORITY_DISABLED.value:
                MainnetTransferCode.POLICY_AUTHORITY_DISABLED,
            RefusalCode.POLICY_VERSION_MISMATCH.value:
                MainnetTransferCode.POLICY_VERSION_MISMATCH,
            RefusalCode.NETWORK_NOT_ALLOWED.value:
                MainnetTransferCode.NETWORK_NOT_ALLOWED,
            RefusalCode.ASSET_NOT_ALLOWED.value:
                MainnetTransferCode.ASSET_NOT_ALLOWED,
            RefusalCode.RECIPIENT_NOT_ALLOWED.value:
                MainnetTransferCode.RECIPIENT_NOT_ALLOWED,
            RefusalCode.AMOUNT_LIMIT_EXCEEDED.value:
                MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED,
            RefusalCode.MAX_FEE_EXCEEDED.value:
                MainnetTransferCode.FEE_LIMIT_EXCEEDED,
        }
        return mapping.get(code, MainnetTransferCode.POLICY_UNAVAILABLE)


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
    code: ReceiptTrackingCode
    endpoint_class: str | None


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
    def l1_fee(self, raw_transaction: bytes) -> int: ...

    def lending_has_code(self, address: str, block: int) -> bool: ...
    def lending_resolve_pool(self, provider: str, block: int) -> str: ...
    def lending_token_decimals(self, token: str, block: int) -> int: ...
    def lending_reserve_a_token(self, provider: str, asset: str, block: int) -> str: ...
    def lending_reserve_configuration(
        self, provider: str, asset: str, block: int,
    ) -> tuple[int, bool, bool]: ...
    def lending_reserve_caps(
        self, provider: str, asset: str, block: int,
    ) -> tuple[int, int]: ...
    def lending_reserve_paused(self, provider: str, asset: str, block: int) -> bool: ...
    def lending_reserve_total_supply(self, provider: str, asset: str, block: int) -> int: ...
    def lending_account_debt(self, pool: str, account: str, block: int) -> int: ...
    def lending_token_balance(self, token: str, account: str, block: int) -> int: ...
    def lending_allowance(
        self, token: str, owner: str, spender: str, block: int,
    ) -> int: ...
    def lending_simulate(self, transaction: Mapping[str, object]) -> bytes: ...
    def lending_protocol_asset(self, target: str, block: int) -> str: ...
    def lending_protocol_paused(self, target: str, action: str, block: int) -> bool: ...
    def lending_compound_borrow(self, target: str, account: str, block: int) -> int: ...
    def lending_vault_limit(self, target: str, method: str, account: str, block: int) -> int: ...
    def lending_vault_convert(self, target: str, method: str, amount: int, block: int) -> int: ...

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
        self._aave = Web3AavePreflightRpc(endpoint, timeout_seconds)

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
        try:
            return Web3.to_hex(self._web3.eth.send_raw_transaction(raw_transaction))
        except Web3RPCError as error:
            # The response is definitive, but may contain provider internals.
            # Preserve only a safe category for Wallet and Hermes.
            raise SubmissionRejectedError() from error
        except (*_TRANSPORT_ERRORS, Web3Exception) as error:
            # Transport and malformed-provider failures remain ambiguous.  The
            # caller fails closed and must never issue a second broadcast.
            raise SubmissionUnknownError() from error

    def l1_fee(self, raw_transaction: bytes) -> int:
        oracle = self._web3.eth.contract(
            address=BASE_GAS_PRICE_ORACLE,
            abi=[{
                "type": "function", "name": "getL1Fee", "stateMutability": "view",
                "inputs": [{"name": "_data", "type": "bytes"}],
                "outputs": [{"name": "", "type": "uint256"}],
            }],
        )
        return int(self._call(lambda: oracle.functions.getL1Fee(raw_transaction).call()))

    def lending_has_code(self, address: str, block: int) -> bool:
        return self._aave.has_code(address, block)

    def lending_resolve_pool(self, provider: str, block: int) -> str:
        return self._aave.resolve_pool(provider, block)

    def lending_token_decimals(self, token: str, block: int) -> int:
        return self._aave.token_decimals(token, block)

    def lending_reserve_a_token(self, provider: str, asset: str, block: int) -> str:
        return self._aave.reserve_a_token(provider, asset, block)

    def lending_reserve_configuration(
        self, provider: str, asset: str, block: int,
    ) -> tuple[int, bool, bool]:
        return self._aave.reserve_configuration(provider, asset, block)

    def lending_reserve_caps(
        self, provider: str, asset: str, block: int,
    ) -> tuple[int, int]:
        return self._aave.reserve_caps(provider, asset, block)

    def lending_reserve_paused(self, provider: str, asset: str, block: int) -> bool:
        return self._aave.reserve_paused(provider, asset, block)

    def lending_reserve_total_supply(self, provider: str, asset: str, block: int) -> int:
        return self._aave.reserve_total_supply(provider, asset, block)

    def lending_account_debt(self, pool: str, account: str, block: int) -> int:
        return self._aave.account_debt(pool, account, block)

    def lending_token_balance(self, token: str, account: str, block: int) -> int:
        return self._aave.token_balance(token, account, block)

    def lending_allowance(
        self, token: str, owner: str, spender: str, block: int,
    ) -> int:
        return self._aave.allowance(token, owner, spender, block)

    def lending_simulate(self, transaction: Mapping[str, object]) -> bytes:
        return self._aave.simulate(transaction)

    def lending_protocol_asset(self, target: str, block: int) -> str:
        return self._aave.protocol_asset(target, block)

    def lending_protocol_paused(self, target: str, action: str, block: int) -> bool:
        return self._aave.protocol_paused(target, action, block)

    def lending_compound_borrow(self, target: str, account: str, block: int) -> int:
        return self._aave.compound_borrow_balance(target, account, block)

    def lending_vault_limit(self, target: str, method: str, account: str, block: int) -> int:
        return self._aave.vault_limit(target, method, account, block)

    def lending_vault_convert(self, target: str, method: str, amount: int, block: int) -> int:
        return self._aave.vault_convert(target, method, amount, block)

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
        self.policy = policy or MainnetBroadcastPolicy.unavailable()
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
        read_rpc, revalidation_code, read_endpoint = _final_revalidation_with_fallback(
            self._rpc_factory, self._environ, action,
        )
        if read_rpc is None:
            return self._failure(action, revalidation_code)
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

            policy_code = _evaluate_policy(self.policy, self.revoke_policy, action)
            if policy_code is not None:
                return self._failure(action, policy_code)

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
            if action.action_type == "lending":
                try:
                    exact_l1_fee = read_rpc.l1_fee(bytes(signed.raw_transaction))
                    total_fee = action.l2_fee_ceiling_wei + exact_l1_fee
                except Exception:
                    return self._failure(
                        action, MainnetTransferCode.REVALIDATION_RPC_UNAVAILABLE,
                    )
                if (
                    exact_l1_fee <= 0
                    or total_fee > action.max_total_fee_wei
                    or int(read_rpc.native_balance(action.sender)) < total_fee
                ):
                    return self._failure(action, MainnetTransferCode.FEE_LIMIT_EXCEEDED)
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

            policy_code = _evaluate_policy(self.policy, self.revoke_policy, action)
            if policy_code is not None:
                return self._result(
                    action, policy_code, transaction_hash, recovered,
                    history_status, False,
                )
            if permit.cancelled:
                return self._result(
                    action, MainnetTransferCode.CANCELLED, transaction_hash,
                    recovered, history_status, False,
                )
            if self._clock().astimezone(UTC) >= action.expires_at:
                return self._result(
                    action, MainnetTransferCode.ACTION_EXPIRED, transaction_hash,
                    recovered, history_status, False,
                )

            broadcast_attempted = True
            try:
                primary_rpc = (
                    read_rpc if read_endpoint == endpoint else self._rpc_factory(endpoint)
                )
                remote_hash = primary_rpc.send_raw_transaction(signed.raw_transaction)
            except (SubmissionRejectedError, Web3RPCError):
                try:
                    self.history_store.update_status(
                        action.action_id,
                        HistoryStatus.FAILED,
                        _timestamp(self._clock()),
                        transaction_hash,
                    )
                    history_status = HistoryStatus.FAILED
                    history_available = True
                except (HistoryUnavailableError, HistoryValidationError, StorageError):
                    history_available = False
                return self._result(
                    action,
                    MainnetTransferCode.SUBMISSION_REJECTED,
                    transaction_hash,
                    recovered,
                    history_status,
                    broadcast_attempted,
                    history_available,
                )
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
            _result_action_type(action),
        )


def _lending_post_state(
    rpc: MainnetRpc, record: WalletHistoryRecord, block: int,
    receipt: Mapping[str, object],
) -> tuple[int, int, bool] | None:
    profile = next(
        (
            item for item in ActionProfilesState.load().profiles
            if item.protocol_id == (record.protocol_id or "aave-v3")
        ),
        None,
    )
    if profile is None:
        return None
    allowance_value = rpc.lending_allowance(
        profile.asset, record.sender, profile.spender, block,
    )
    if type(allowance_value) is not int or allowance_value < 0:
        return None
    allowance = allowance_value
    if profile.protocol_id == "morpho-v1":
        shares_value = rpc.lending_token_balance(
            profile.position_token, record.sender, block,
        )
        position_value = rpc.lending_vault_convert(
            profile.target, "convertToAssets", shares_value, block,
        ) if type(shares_value) is int and shares_value >= 0 else None
        if type(shares_value) is not int or type(position_value) is not int:
            return None
        if shares_value < 0 or position_value < 0:
            return None
        shares = shares_value
        position = position_value
    else:
        shares = -1
        position_value = rpc.lending_token_balance(
            profile.position_token, record.sender, block,
        )
        if type(position_value) is not int or position_value < 0:
            return None
        position = position_value
    amount = int(record.amount_atomic)
    before = int(record.position_before_atomic or "0")
    if record.action_type == "lending_approve":
        verified = allowance == amount
    elif record.action_type in {"lending_supply", "lending_deposit"}:
        if profile.protocol_id == "aave-v3":
            # Aave aToken balanceOf derives from scaled shares and may floor
            # below the supplied USDC amount in the receipt block.
            verified = (
                allowance == 0
                and position >= before
                and _matching_aave_supply_log(receipt, record, profile)
            )
        else:
            verified = allowance == 0 and position >= before + amount - 1
    elif record.action_type in {"lending_withdraw_all", "lending_redeem"}:
        verified = allowance == 0 and (
            shares == 0 if record.action_type == "lending_redeem" else position <= 1
        )
    elif record.action_type == "lending_withdraw":
        verified = allowance == 0 and position + amount <= before + 10
    else:
        return None
    return position, allowance, verified


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
        if (
            self.timeout_seconds > 0
            and
            result.status is HistoryStatus.PENDING
            and not (cancelled is not None and cancelled.is_set())
            and self._monotonic() >= deadline
        ):
            record = next(
                (
                    item for item in self.history_store.load()
                    if item.action_id == action_id
                ),
                None,
            )
            if record is not None:
                result = self._save_observation(
                    record,
                    HistoryStatus.UNKNOWN,
                    ReceiptTrackingCode.TRANSACTION_UNKNOWN,
                    result.endpoint_class,
                )
        return result

    def check_once(self, action_id: str) -> ReceiptTrackingResult:
        records = self.history_store.load()
        record = next((item for item in records if item.action_id == action_id), None)
        if record is None or record.transaction_hash is None:
            raise HistoryValidationError("History action cannot be checked")
        if record.status in {HistoryStatus.CONFIRMED, HistoryStatus.FAILED}:
            code = _stored_receipt_code(record) or _receipt_code(record.status)
            return self._result(
                record, record.status, True, code, record.receipt_endpoint_class,
            )
        candidates = _receipt_endpoints(self._environ, record.network)
        if not candidates:
            return self._save_observation(
                record, HistoryStatus.UNKNOWN,
                ReceiptTrackingCode.RPC_UNAVAILABLE, None,
            )
        pending: tuple[str, MainnetRpc] | None = None
        transaction_unknown: tuple[str, MainnetRpc] | None = None
        last_failure = ReceiptTrackingCode.RPC_UNAVAILABLE
        last_failure_class: str | None = candidates[-1][0]
        for endpoint_class, endpoint in candidates:
            try:
                rpc = self._rpc_factory(endpoint)
                if rpc.chain_id() != record.chain_id:
                    last_failure = ReceiptTrackingCode.WRONG_CHAIN
                    last_failure_class = endpoint_class
                    continue
                receipt = rpc.transaction_receipt(record.transaction_hash)
                if receipt is None:
                    if record.status is HistoryStatus.PENDING:
                        pending = pending or (endpoint_class, rpc)
                        continue
                    transaction = rpc.transaction(record.transaction_hash)
                    if transaction is None:
                        transaction_unknown = transaction_unknown or (endpoint_class, rpc)
                        continue
                    if not _public_transaction_matches(transaction, record):
                        return self._save_observation(
                            record, HistoryStatus.UNKNOWN,
                            ReceiptTrackingCode.VALIDATION_FAILED, endpoint_class,
                        )
                    pending = pending or (endpoint_class, rpc)
                    continue
                if not isinstance(receipt, Mapping):
                    return self._save_observation(
                        record, HistoryStatus.UNKNOWN,
                        ReceiptTrackingCode.VALIDATION_FAILED, endpoint_class,
                    )
                observation = self._receipt_observation(
                    record, rpc, receipt, endpoint_class,
                )
            except Exception:
                last_failure = ReceiptTrackingCode.RPC_UNAVAILABLE
                last_failure_class = endpoint_class
                continue
            if observation[0] is HistoryStatus.UNKNOWN:
                return self._save_observation(record, *observation)
            return self._save_observation(record, *observation)
        if pending is not None:
            return self._save_observation(
                record, HistoryStatus.PENDING, ReceiptTrackingCode.PENDING,
                pending[0],
            )
        if transaction_unknown is not None:
            return self._save_observation(
                record, HistoryStatus.UNKNOWN,
                ReceiptTrackingCode.TRANSACTION_UNKNOWN, transaction_unknown[0],
            )
        return self._save_observation(
            record, HistoryStatus.UNKNOWN, last_failure, last_failure_class,
        )

    def _receipt_observation(
        self,
        record: WalletHistoryRecord,
        rpc: MainnetRpc,
        receipt: Mapping[str, object],
        endpoint_class: str,
    ) -> tuple[
        HistoryStatus, ReceiptTrackingCode, str,
        str | None, str | None, str | None, bool | None,
    ]:
        transaction = (
            rpc.transaction(record.transaction_hash or "")
            if (
                record.token == "ETH"
                or record.action_type == REVOKE_ACTION_TYPE
                or record.action_type.startswith("lending_")
            )
            else None
        )
        observed = _receipt_status(receipt, record, transaction)
        if observed is HistoryStatus.UNKNOWN:
            return (
                observed, ReceiptTrackingCode.VALIDATION_FAILED, endpoint_class,
                None, None, None, None,
            )
        position_after_atomic: str | None = None
        allowance_after_atomic: str | None = None
        position_verified: bool | None = None
        if (
            observed is HistoryStatus.CONFIRMED
            and record.action_type.startswith("lending_")
            and (record.protocol_id is not None or record.action_type == "lending_supply")
        ):
            block = receipt.get("blockNumber")
            if type(block) is not int:
                return (
                    HistoryStatus.UNKNOWN, ReceiptTrackingCode.VALIDATION_FAILED,
                    endpoint_class, None, None, None, None,
                )
            post_state = _lending_post_state(rpc, record, block, receipt)
            if post_state is None:
                return (
                    HistoryStatus.UNKNOWN, ReceiptTrackingCode.VALIDATION_FAILED,
                    endpoint_class, None, None, None, None,
                )
            position_after, allowance_after, verified = post_state
            position_after_atomic = str(position_after)
            allowance_after_atomic = str(allowance_after)
            position_verified = verified
            if not verified:
                return (
                    HistoryStatus.UNKNOWN, ReceiptTrackingCode.VALIDATION_FAILED,
                    endpoint_class, None, position_after_atomic,
                    allowance_after_atomic, False,
                )
        code = (
            ReceiptTrackingCode.CONFIRMED
            if observed is HistoryStatus.CONFIRMED
            else ReceiptTrackingCode.FAILED
        )
        return (
            observed, code, endpoint_class, _receipt_fee_wei(receipt),
            position_after_atomic, allowance_after_atomic, position_verified,
        )

    def _save_observation(
        self,
        record: WalletHistoryRecord,
        observed: HistoryStatus,
        code: ReceiptTrackingCode,
        endpoint_class: str | None,
        actual_fee_wei: str | None = None,
        position_after_atomic: str | None = None,
        allowance_after_atomic: str | None = None,
        position_verified: bool | None = None,
    ) -> ReceiptTrackingResult:
        if (
            observed is record.status
            and code.value == record.receipt_code
            and endpoint_class == record.receipt_endpoint_class
            and (actual_fee_wei is None or record.actual_fee_wei is not None)
            and (position_after_atomic is None or record.position_after_atomic is not None)
            and (allowance_after_atomic is None or record.allowance_after_atomic is not None)
            and (position_verified is None or record.position_verified is not None)
        ):
            return self._result(record, observed, True, code, endpoint_class)
        try:
            updated = self.history_store.update_status(
                record.action_id,
                observed,
                _timestamp(self._clock()),
                record.transaction_hash,
                actual_fee_wei,
                position_after_atomic,
                allowance_after_atomic,
                position_verified,
                code.value,
                endpoint_class,
            )
            current = next(item for item in updated if item.action_id == record.action_id)
            return self._result(current, observed, True, code, endpoint_class)
        except (HistoryUnavailableError, HistoryValidationError, StorageError):
            return self._result(record, observed, False, code, endpoint_class)

    def _result(
        self,
        record: WalletHistoryRecord,
        status: HistoryStatus,
        history_available: bool,
        code: ReceiptTrackingCode,
        endpoint_class: str | None,
    ) -> ReceiptTrackingResult:
        return ReceiptTrackingResult(
            record.action_id,
            record.transaction_hash or "",
            status,
            _timestamp(self._clock()),
            history_available,
            code,
            endpoint_class,
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
        ReceiptTrackingCode.CONFIRMED: MainnetTransferCode.CONFIRMED,
        ReceiptTrackingCode.FAILED: MainnetTransferCode.FAILED,
        ReceiptTrackingCode.PENDING: MainnetTransferCode.PENDING,
        ReceiptTrackingCode.RPC_UNAVAILABLE:
            MainnetTransferCode.RECEIPT_RPC_UNAVAILABLE,
        ReceiptTrackingCode.WRONG_CHAIN:
            MainnetTransferCode.RECEIPT_WRONG_CHAIN,
        ReceiptTrackingCode.VALIDATION_FAILED:
            MainnetTransferCode.RECEIPT_VALIDATION_FAILED,
        ReceiptTrackingCode.TRANSACTION_UNKNOWN: MainnetTransferCode.UNKNOWN,
    }[tracking.code]
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
    if (
        isinstance(action, PreparedTransferAction)
        and action.action_type == "perpdex_funding"
        and action.protocol_id == "hyperliquid-bridge2"
    ):
        return None
    if not isinstance(action, PreparedRevokeAction):
        return transfer_policy.evaluate(action)
    code = revoke_policy.evaluate(action)
    if code is RevokePolicyCode.FEE_LIMIT_EXCEEDED:
        return MainnetTransferCode.FEE_LIMIT_EXCEEDED
    if code is not None:
        return MainnetTransferCode.POLICY_UNAVAILABLE
    return None


def _final_revalidation_with_fallback(
    rpc_factory: MainnetRpcFactory,
    environ: Mapping[str, str],
    action: PreparedTransactionAction,
) -> tuple[MainnetRpc | None, MainnetTransferCode, str | None]:
    """Read-only revalidation may fail over; broadcast never does."""
    for _endpoint_class, endpoint in _receipt_endpoints(environ, action.network_id):
        try:
            rpc = rpc_factory(endpoint)
            if _final_revalidation(rpc, action):
                return rpc, MainnetTransferCode.CONFIRMED, endpoint
        except Exception:
            continue
        return None, MainnetTransferCode.REVALIDATION_FAILED, None
    return None, MainnetTransferCode.REVALIDATION_RPC_UNAVAILABLE, None


def _final_revalidation(rpc: MainnetRpc, action: PreparedTransactionAction) -> bool:
    if isinstance(action, PreparedRevokeAction):
        return _final_revoke_revalidation(rpc, action)
    if action.action_type == "lending":
        return _final_lending_revalidation(rpc, action)
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


def _final_protocol_revalidation(
    rpc: MainnetRpc, action: PreparedTransferAction, profile,
) -> bool:
    tx = action.transaction
    call_amount = action.call_amount_atomic or action.amount_atomic
    try:
        block, base_fee = rpc.latest_block()
        if (
            not rpc.lending_has_code(profile.asset, block)
            or not rpc.lending_has_code(profile.target, block)
            or rpc.lending_token_decimals(profile.asset, block) != profile.decimals
            or rpc.lending_protocol_asset(profile.target, block) != profile.asset
        ):
            return False
        balance = int(rpc.lending_token_balance(profile.asset, action.sender, block))
        allowance = int(rpc.lending_allowance(
            profile.asset, action.sender, profile.spender, block,
        ))
        if profile.protocol_id == "compound-v3":
            requested = "withdraw" if action.method == "withdraw" else "supply"
            if (
                rpc.lending_protocol_paused(profile.target, requested, block)
                or rpc.lending_compound_borrow(profile.target, action.sender, block) != 0
            ):
                return False
            position = int(rpc.lending_token_balance(profile.position_token, action.sender, block))
            liquidity = int(rpc.lending_token_balance(profile.asset, profile.target, block))
        else:
            dead = "0x000000000000000000000000000000000000dEaD"
            if rpc.lending_token_balance(profile.position_token, dead, block) < 10**12:
                return False
            shares = position = liquidity = 0
            if action.method in {"withdraw", "redeem"}:
                shares = int(rpc.lending_token_balance(
                    profile.position_token, action.sender, block,
                ))
                position = int(rpc.lending_vault_convert(
                    profile.target, "convertToAssets", shares, block,
                ))
                liquidity = int(rpc.lending_vault_limit(
                    profile.target, "maxWithdraw", action.sender, block,
                ))
        if action.method == "approve":
            expected_data = encode_approve(profile.spender, action.amount_atomic)
            state_ok = allowance == 0 and balance >= action.amount_atomic
        elif action.method == "supply":
            expected_data = encode_compound_supply(profile.asset, action.amount_atomic)
            state_ok = allowance == action.amount_atomic and balance >= action.amount_atomic
        elif action.method == "deposit":
            expected_data = encode_morpho_deposit(action.amount_atomic, action.sender)
            state_ok = (
                allowance == action.amount_atomic and balance >= action.amount_atomic
                and rpc.lending_vault_limit(
                    profile.target, "maxDeposit", action.sender, block,
                ) >= action.amount_atomic
            )
        elif action.method == "withdraw" and profile.protocol_id == "compound-v3":
            expected_data = encode_compound_withdraw(profile.asset, call_amount)
            state_ok = liquidity >= action.amount_atomic and position >= action.amount_atomic
        elif action.method == "withdraw":
            expected_data = encode_morpho_withdraw(action.amount_atomic, action.sender)
            state_ok = position >= action.amount_atomic and liquidity >= action.amount_atomic
        elif action.method == "redeem":
            expected_data = encode_morpho_redeem(call_amount, action.sender)
            state_ok = (
                shares == call_amount and position >= action.amount_atomic
                and liquidity >= action.amount_atomic
            )
        else:
            return False
        estimate = int(rpc.estimate_gas({
            "from": action.sender, "to": tx.to, "value": 0, "data": tx.data,
            "nonce": tx.nonce, "type": 2, "chainId": tx.chain_id,
            "maxFeePerGas": tx.max_fee_per_gas,
            "maxPriorityFeePerGas": tx.max_priority_fee_per_gas,
        }))
        priority = int(rpc.max_priority_fee_per_gas())
        native = int(rpc.native_balance(action.sender))
        rpc.lending_simulate({
            "from": action.sender, "to": tx.to, "value": 0, "data": tx.data,
            "nonce": tx.nonce, "type": 2, "chainId": tx.chain_id,
            "maxFeePerGas": tx.max_fee_per_gas,
            "maxPriorityFeePerGas": tx.max_priority_fee_per_gas, "gas": tx.gas,
        })
    except Exception:
        raise
    expected_target = profile.asset if action.method == "approve" else profile.target
    return (
        state_ok and rpc.chain_id() == profile.chain_id and block >= action.block_number
        and tx.to.lower() == expected_target.lower()
    ) and (
        tx.data == expected_data and rpc.pending_nonce(action.sender) == tx.nonce
        and 0 < estimate <= tx.gas and 0 <= priority <= tx.max_priority_fee_per_gas
        and 0 < 2 * int(base_fee) + priority <= tx.max_fee_per_gas
        and native >= action.max_total_fee_wei
    )


def _final_lending_revalidation(rpc: MainnetRpc, action: PreparedTransferAction) -> bool:
    profiles = ActionProfilesState.load()
    profile = profiles.select_by_digest(action.action_profile_digest)
    if profile is None:
        return False
    if action.protocol_id != profile.protocol_id:
        return False
    if profile.protocol_id != "aave-v3":
        return _final_protocol_revalidation(rpc, action, profile)
    tx = action.transaction
    try:
        block, base_fee = rpc.latest_block()
        if any(not rpc.lending_has_code(address, block) for address in (
            profile.asset, profile.pool, profile.provider,
            profile.data_provider, profile.a_token,
        )):
            return False
        decimals, active, frozen = rpc.lending_reserve_configuration(
            profile.data_provider, profile.asset, block,
        )
        if (
            rpc.lending_resolve_pool(profile.provider, block) != profile.pool
            or rpc.lending_token_decimals(profile.asset, block) != profile.decimals
            or rpc.lending_reserve_a_token(
                profile.data_provider, profile.asset, block,
            ) != profile.a_token
            or decimals != profile.decimals or not active or frozen
            or rpc.lending_reserve_paused(
                profile.data_provider, profile.asset, block,
            )
            or rpc.lending_account_debt(profile.pool, action.sender, block) != 0
        ):
            return False
        allowance = (
            int(rpc.lending_allowance(
                profile.asset, action.sender, profile.pool, block,
            ))
            if action.method in {"approve", "supply"}
            else None
        )
        if action.method == "approve":
            expected_data = encode_approve(profile.pool, action.amount_atomic)
        elif action.method == "supply":
            expected_data = encode_supply(
                profile.asset, action.amount_atomic, action.sender,
            )
        else:
            expected_data = encode_withdraw(
                profile.asset,
                MAX_UINT256 if action.amount_mode == "all" else action.amount_atomic,
                action.sender,
            )
        expected_target = profile.asset if action.method == "approve" else profile.pool
        estimate = int(rpc.estimate_gas({
            "from": action.sender, "to": tx.to, "value": 0, "data": tx.data,
            "nonce": tx.nonce, "type": 2, "chainId": tx.chain_id,
            "maxFeePerGas": tx.max_fee_per_gas,
            "maxPriorityFeePerGas": tx.max_priority_fee_per_gas,
        }))
        priority = int(rpc.max_priority_fee_per_gas())
        balance = int(rpc.lending_token_balance(
            profile.asset, action.sender, block,
        ))
        if action.method == "withdraw":
            position = int(rpc.lending_token_balance(
                profile.a_token, action.sender, block,
            ))
            liquidity = int(rpc.lending_token_balance(
                profile.asset, profile.a_token, block,
            ))
            supply_cap = total_supply = 0
        else:
            position = liquidity = 0
            _borrow_cap, supply_cap = rpc.lending_reserve_caps(
                profile.data_provider, profile.asset, block,
            )
            total_supply = rpc.lending_reserve_total_supply(
                profile.data_provider, profile.asset, block,
            )
        native = int(rpc.native_balance(action.sender))
        rpc.lending_simulate({
            "from": action.sender, "to": tx.to, "value": 0, "data": tx.data,
            "nonce": tx.nonce, "type": 2, "chainId": tx.chain_id,
            "maxFeePerGas": tx.max_fee_per_gas,
            "maxPriorityFeePerGas": tx.max_priority_fee_per_gas, "gas": tx.gas,
        })
    except Exception:
        raise
    supply_state = (
        balance >= action.amount_atomic
        and (
            supply_cap == 0
            or total_supply + action.amount_atomic <= supply_cap * 10**profile.decimals
        )
        and ((action.method == "approve" and allowance == 0)
             or (action.method == "supply" and allowance == action.amount_atomic))
    )
    withdraw_state = (
        action.method == "withdraw"
        and liquidity >= action.amount_atomic
        and (
            position >= action.amount_atomic
            if action.amount_mode == "all"
            else position >= action.amount_atomic
        )
    )
    return (
        rpc.chain_id() == profile.chain_id and block >= action.block_number
        and rpc.token_decimals(profile.asset) == 6
        and (withdraw_state if action.method == "withdraw" else supply_state)
        and tx.to.lower() == expected_target.lower() and tx.data == expected_data
        and rpc.pending_nonce(action.sender) == tx.nonce
        and 0 < estimate <= tx.gas and 0 <= priority <= tx.max_priority_fee_per_gas
        and 0 < 2 * int(base_fee) + priority <= tx.max_fee_per_gas
        and native >= action.max_total_fee_wei
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
        raise
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
        if (
            record.token == "ETH" or record.action_type == REVOKE_ACTION_TYPE
            or record.action_type.startswith("lending_")
        ):
            if transaction is None or not _public_transaction_matches(transaction, record):
                return HistoryStatus.UNKNOWN
        if record.action_type.startswith("lending_"):
            return HistoryStatus.CONFIRMED
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


def _matching_aave_supply_log(
    receipt: Mapping[str, object], record: WalletHistoryRecord, profile: object,
) -> bool:
    """Match Aave's exact Supply event without trusting scaled aToken rounding."""
    try:
        target = str(getattr(profile, "target"))
        asset = str(getattr(profile, "asset"))
        logs = receipt["logs"]
        if not isinstance(logs, (list, tuple)):
            return False
        asset_topic = "0x" + asset[2:].lower().rjust(64, "0")
        account_topic = "0x" + record.sender[2:].lower().rjust(64, "0")
        referral_topic = "0x" + "0".rjust(64, "0")
        for value in logs:
            if not isinstance(value, Mapping) or str(value["address"]).lower() != target.lower():
                continue
            topics = value["topics"]
            if not isinstance(topics, (list, tuple)) or len(topics) != 4:
                continue
            rendered = [_hex_value(topic).lower() for topic in topics]
            data = HexBytes(value["data"])
            if len(data) != 64:
                continue
            user = "0x" + data[:32].hex()
            amount = int.from_bytes(data[32:], "big")
            if (
                rendered[0] == AAVE_SUPPLY_EVENT_TOPIC.lower()
                and rendered[1] == asset_topic
                and rendered[2] == account_topic
                and rendered[3] == referral_topic
                and user == account_topic
                and amount == int(record.amount_atomic)
            ):
                return True
    except (AttributeError, KeyError, TypeError, ValueError):
        return False
    return False


def _receipt_fee_wei(receipt: Mapping[str, object]) -> str | None:
    try:
        gas_used = int(receipt["gasUsed"])
        effective_gas_price = int(receipt["effectiveGasPrice"])
        l1_fee_value = receipt.get("l1Fee", 0)
        l1_fee = (
            int(l1_fee_value, 16)
            if isinstance(l1_fee_value, str) and l1_fee_value.startswith("0x")
            else int(l1_fee_value)
        )
        if gas_used < 0 or effective_gas_price < 0 or l1_fee < 0:
            return None
        return str(gas_used * effective_gas_price + l1_fee)
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
        elif record.action_type == "lending_approve":
            expected_data = encode_approve(record.recipient, int(record.amount_atomic))
            expected_value = 0
        elif record.action_type.startswith("lending_"):
            profile = next((
                item for item in ActionProfilesState.load().profiles
                if item.protocol_id == (record.protocol_id or "aave-v3")
            ), None)
            if profile is None:
                return False
            amount = int(record.amount_atomic)
            call_amount = int(
                record.call_amount_atomic
                or (str(MAX_UINT256) if record.action_type == "lending_withdraw_all" else record.amount_atomic)
            )
            if record.action_type == "lending_supply" and profile.protocol_id == "aave-v3":
                expected_data = encode_supply(profile.asset, amount, record.sender)
            elif record.action_type == "lending_supply":
                expected_data = encode_compound_supply(profile.asset, amount)
            elif record.action_type == "lending_deposit":
                expected_data = encode_morpho_deposit(amount, record.sender)
            elif record.action_type in {"lending_withdraw", "lending_withdraw_all"} and profile.protocol_id == "aave-v3":
                expected_data = encode_withdraw(profile.asset, call_amount, record.sender)
            elif record.action_type in {"lending_withdraw", "lending_withdraw_all"}:
                expected_data = encode_compound_withdraw(profile.asset, call_amount)
            elif record.action_type == "lending_redeem":
                expected_data = encode_morpho_redeem(call_amount, record.sender)
            else:
                return False
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
    network = NETWORK_BY_ID.get(network_id)
    if network is None:
        return None
    value = environ.get(network.endpoint_env, network.default_endpoint)
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _receipt_endpoints(
    environ: Mapping[str, str], network_id: str,
) -> tuple[tuple[str, str], ...]:
    if network_id != BASE_NETWORK_ID:
        endpoint = _endpoint(environ, network_id)
        return (("configured", endpoint),) if endpoint is not None else ()
    configured = environ.get(BASE_RPC_ENV, "").strip()
    candidates = (
        (("configured", configured),) if configured else ()
    ) + (
        ("official", DEFAULT_BASE_RPC_URL),
        ("alchemy_public", ALCHEMY_BASE_RPC_URL),
    )
    seen: set[str] = set()
    result: list[tuple[str, str]] = []
    for endpoint_class, endpoint in candidates:
        normalized = endpoint.rstrip("/").lower()
        if normalized in seen:
            continue
        seen.add(normalized)
        result.append((endpoint_class, endpoint))
    return tuple(result)


def _stored_receipt_code(record: WalletHistoryRecord) -> ReceiptTrackingCode | None:
    try:
        return ReceiptTrackingCode(record.receipt_code)
    except (TypeError, ValueError):
        return None


def _receipt_code(status: HistoryStatus) -> ReceiptTrackingCode:
    return {
        HistoryStatus.PENDING: ReceiptTrackingCode.PENDING,
        HistoryStatus.CONFIRMED: ReceiptTrackingCode.CONFIRMED,
        HistoryStatus.FAILED: ReceiptTrackingCode.FAILED,
        HistoryStatus.UNKNOWN: ReceiptTrackingCode.TRANSACTION_UNKNOWN,
        HistoryStatus.PREPARED: ReceiptTrackingCode.TRANSACTION_UNKNOWN,
    }[status]


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
        MainnetTransferCode.SUBMISSION_REJECTED: (
            "Submission rejected",
            "The configured Base provider rejected the transaction. Nothing was accepted or sent again.",
        ),
        MainnetTransferCode.RECEIPT_RPC_UNAVAILABLE: (
            "Receipt RPC unavailable",
            "Broadcast already occurred once. Receipt checks failed; no rebroadcast will occur.",
        ),
        MainnetTransferCode.RECEIPT_WRONG_CHAIN: (
            "Receipt network mismatch",
            "Broadcast already occurred once. No trusted Base receipt was accepted.",
        ),
        MainnetTransferCode.RECEIPT_VALIDATION_FAILED: (
            "Receipt could not be verified",
            "Broadcast already occurred once. Conflicting or incomplete data was rejected.",
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
            "The verified local transfer policy is unavailable.",
        ),
        MainnetTransferCode.POLICY_AUTHORITY_DISABLED: (
            "Mainnet sending disabled",
            "Transfers are disabled by the verified local policy.",
        ),
        MainnetTransferCode.POLICY_VERSION_MISMATCH: (
            "Policy version changed",
            "Nothing was sent. Prepare a new action under the current policy.",
        ),
        MainnetTransferCode.NETWORK_NOT_ALLOWED: (
            "Network not allowed",
            "Nothing was sent. The selected network is not allowed by policy.",
        ),
        MainnetTransferCode.ASSET_NOT_ALLOWED: (
            "Asset not allowed",
            "Nothing was sent. The selected asset is not allowed by policy.",
        ),
        MainnetTransferCode.RECIPIENT_NOT_ALLOWED: (
            "Recipient not allowed",
            "Nothing was sent. The recipient is not allowed by policy.",
        ),
        MainnetTransferCode.FEE_LIMIT_EXCEEDED: (
            "Fee limit exceeded",
            "Nothing was sent. Prepare a new action when fees are lower.",
        ),
        MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED: (
            "Amount limit exceeded",
            "Nothing was sent. The transfer exceeds its recipient or route limit.",
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
        MainnetTransferCode.REVALIDATION_RPC_UNAVAILABLE: (
            "Live revalidation unavailable",
            "Nothing was sent. Read-only Base RPC checks could not be completed.",
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
    if action_type.startswith("lending_") or action_type.startswith("lending:"):
        if action_type.startswith("lending:"):
            _prefix, protocol_id, action = action_type.split(":", 2)
        else:
            protocol_id, action = "aave-v3", action_type.removeprefix("lending_")
        protocol = {
            "aave-v3": "Aave V3", "compound-v3": "Compound III", "morpho-v1": "Morpho V1",
        }.get(protocol_id, "Lending")
        approval_protocol = "Aave" if protocol_id == "aave-v3" else protocol
        label = {
            "approve": f"{protocol} approval",
            "supply": f"{protocol} supply", "deposit": f"{protocol} supply",
            "withdraw": f"{protocol} withdrawal", "withdraw_all": f"{protocol} withdrawal",
            "redeem": f"{protocol} withdrawal",
        }.get(action, f"{protocol} action")
        replacements = {
            MainnetTransferCode.CONFIRMED: {
                "approve": (
                    f"{approval_protocol} approval confirmed",
                    "Approval confirmed · Preparing the separate Supply Review…",
                ),
                "supply": (
                    f"Supplied to {protocol}",
                    "The exact reviewed USDC amount was supplied on-chain.",
                ),
                "deposit": (
                    f"Supplied to {protocol}",
                    "The exact reviewed USDC amount was supplied on-chain.",
                ),
                "withdraw": (
                    f"Withdrawn from {protocol}",
                    "The exact reviewed USDC amount returned to this Wallet.",
                ),
                "withdraw_all": (
                    f"Withdrawn from {protocol}",
                    "The reviewed Lending position returned to this Wallet.",
                ),
                "redeem": (
                    f"Withdrawn from {protocol}",
                    "The reviewed Lending position returned to this Wallet.",
                ),
            }.get(
                action,
                (
                    f"{protocol} action confirmed",
                    "The reviewed action was confirmed on-chain.",
                ),
            ),
            MainnetTransferCode.PENDING: (
                f"{label} submitted",
                "Broadcast occurred once. Confirmation is still pending.",
            ),
            MainnetTransferCode.FAILED: (
                f"{label} reverted",
                "A network fee may have been spent, but the Lending action reverted.",
            ),
        }
        return replacements.get(code, (title, message))
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


def _result_action_type(action: PreparedTransactionAction) -> str:
    if isinstance(action, PreparedRevokeAction):
        return REVOKE_ACTION_TYPE
    if action.action_type != "lending":
        return action.action_type
    legacy = (
        "lending_withdraw_all"
        if action.method == "withdraw" and action.amount_mode == "all"
        else f"lending_{action.method}"
    )
    if not action.protocol_id or action.protocol_id == "aave-v3":
        return legacy
    method = "withdraw_all" if action.method == "withdraw" and action.amount_mode == "all" else action.method
    return f"lending:{action.protocol_id}:{method}"


_TRANSPORT_ERRORS = (
    request_errors.ConnectionError,
    request_errors.Timeout,
    request_errors.HTTPError,
    TimeoutError,
)
