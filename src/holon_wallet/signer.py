"""Single-use offline signing for one exact allowlisted transfer."""

from __future__ import annotations

import hmac
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from enum import Enum
from typing import Callable

from eth_account import Account
from eth_account.typed_transactions import TypedTransaction
from hexbytes import HexBytes
from web3 import Web3

from .approval import (
    REVOKE_ACTION_TYPE,
    REVOKE_LIFETIME,
    REVOKE_SCHEMA_VERSION,
    PreparedRevokeAction,
    approval_route,
    encode_usdc_approve_zero,
)
from .transfer import (
    ACTION_LIFETIME,
    BASE_NETWORK_ID,
    TRANSFER_SCHEMA_VERSION,
    PreparedTransferAction,
    SigningPermit,
    encode_usdc_transfer,
    transfer_route,
)
from .vault import (
    AuthenticationFailedError,
    VaultRepository,
    VaultUnavailableError,
)
from .wallet_crypto import InvalidSecretError, private_key_bytes, rederive

FEE_LIMIT_ENV = "HOLON_BASE_MAX_TOTAL_FEE_WEI"
FEE_LIMIT_ENVS = {
    "base": FEE_LIMIT_ENV,
    "ethereum": "HOLON_ETHEREUM_MAX_TOTAL_FEE_WEI",
}
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,77}$")
PreparedTransactionAction = PreparedTransferAction | PreparedRevokeAction


class OfflineSigningCode(str, Enum):
    SUCCESS = "SUCCESS"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    POLICY_UNAVAILABLE = "POLICY_UNAVAILABLE"
    FEE_LIMIT_EXCEEDED = "FEE_LIMIT_EXCEEDED"
    ACTION_INVALID = "ACTION_INVALID"
    ACTION_EXPIRED = "ACTION_EXPIRED"
    CANCELLED = "CANCELLED"
    SIGNING_FAILED = "SIGNING_FAILED"


@dataclass(frozen=True, slots=True)
class OfflineSigningPolicy:
    max_total_fee_wei: int | None

    def __post_init__(self) -> None:
        if self.max_total_fee_wei is not None and (
            type(self.max_total_fee_wei) is not int or self.max_total_fee_wei <= 0
        ):
            raise ValueError("Offline signing fee limit is invalid")

    @classmethod
    def from_environment(
        cls,
        environ: Mapping[str, str] | None = None,
        network_id: str = BASE_NETWORK_ID,
    ) -> OfflineSigningPolicy:
        source = os.environ if environ is None else environ
        value = source.get(FEE_LIMIT_ENVS.get(network_id, ""), "").strip()
        if DECIMAL_RE.fullmatch(value) is None:
            return cls(None)
        return cls(int(value))

    @property
    def available(self) -> bool:
        return self.max_total_fee_wei is not None

    @property
    def display(self) -> str:
        if self.max_total_fee_wei is None:
            return "Not configured"
        return f"≤ {_format_wei(self.max_total_fee_wei)} ETH"

    def evaluate(self, action: PreparedTransactionAction) -> OfflineSigningCode | None:
        if self.max_total_fee_wei is None:
            return OfflineSigningCode.POLICY_UNAVAILABLE
        if action.max_total_fee_wei > self.max_total_fee_wei:
            return OfflineSigningCode.FEE_LIMIT_EXCEEDED
        return None


@dataclass(frozen=True, slots=True)
class OfflineSigningResult:
    success: bool
    code: OfflineSigningCode
    action_id: str
    digest: str
    transaction_hash: str
    recovered_signer: str
    completed_at: str
    simulation: bool


class OfflineTransferSigner:
    """Authenticates, signs, verifies, and discards one raw transaction locally."""

    def __init__(
        self,
        repository: VaultRepository,
        policy: OfflineSigningPolicy | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.policy = policy or OfflineSigningPolicy.from_environment()
        self._clock = clock or (lambda: datetime.now(UTC))

    def sign(
        self,
        action: PreparedTransactionAction,
        expected_digest: str,
        password: str,
        permit: SigningPermit,
    ) -> OfflineSigningResult:
        code = self._validate_action(action, expected_digest)
        if code is not None:
            return self._failure(action, code)
        policy_code = self.policy.evaluate(action)
        if policy_code is not None:
            return self._failure(action, policy_code)
        if permit.cancelled:
            return self._failure(action, OfflineSigningCode.CANCELLED)

        private_key: bytearray | None = None
        signed = None
        decoded = None
        try:
            record = self.repository._authenticate_profile(password, action.profile_id)
            if permit.cancelled:
                return self._failure(action, OfflineSigningCode.CANCELLED)
            if self._clock().astimezone(UTC) >= action.expires_at:
                return self._failure(action, OfflineSigningCode.ACTION_EXPIRED)
            if (
                record.summary.profile_id != action.profile_id
                or not hmac.compare_digest(
                    record.summary.address.lower(), action.sender.lower(),
                )
                or not hmac.compare_digest(
                    rederive(record.secret).lower(), action.sender.lower(),
                )
            ):
                return self._failure(action, OfflineSigningCode.ACTION_INVALID)

            private_key = bytearray(private_key_bytes(record.secret))
            if permit.cancelled:
                return self._failure(action, OfflineSigningCode.CANCELLED)
            if self._clock().astimezone(UTC) >= action.expires_at:
                return self._failure(action, OfflineSigningCode.ACTION_EXPIRED)

            transaction = transaction_dict(action)
            signed = Account.sign_transaction(transaction, bytes(private_key))
            recovered = Web3.to_checksum_address(
                Account.recover_transaction(signed.raw_transaction),
            )
            decoded = TypedTransaction.from_bytes(
                HexBytes(signed.raw_transaction),
            ).as_dict()
            if (
                permit.cancelled
                or recovered.lower() != action.sender.lower()
                or not decoded_transaction_matches(decoded, action)
            ):
                return self._failure(
                    action,
                    OfflineSigningCode.CANCELLED
                    if permit.cancelled else OfflineSigningCode.SIGNING_FAILED,
                )
            return OfflineSigningResult(
                True,
                OfflineSigningCode.SUCCESS,
                action.action_id,
                action.digest,
                "0x" + signed.hash.hex(),
                recovered,
                _timestamp(self._clock()),
                action.simulation,
            )
        except (AuthenticationFailedError, VaultUnavailableError, InvalidSecretError):
            return self._failure(action, OfflineSigningCode.AUTHENTICATION_FAILED)
        except Exception:
            return self._failure(action, OfflineSigningCode.SIGNING_FAILED)
        finally:
            if private_key is not None:
                for index in range(len(private_key)):
                    private_key[index] = 0
            del private_key, signed, decoded

    def _validate_action(
        self, action: PreparedTransactionAction, expected_digest: str,
    ) -> OfflineSigningCode | None:
        return validate_signing_action(
            action,
            expected_digest,
            self._clock().astimezone(UTC),
        )

    def _failure(
        self, action: PreparedTransactionAction, code: OfflineSigningCode,
    ) -> OfflineSigningResult:
        return OfflineSigningResult(
            False,
            code,
            action.action_id,
            action.digest,
            "",
            "",
            _timestamp(self._clock()),
            action.simulation,
        )


def validate_signing_action(
    action: PreparedTransactionAction,
    expected_digest: str,
    now: datetime,
) -> OfflineSigningCode | None:
    now = now.astimezone(UTC)
    if now >= action.expires_at:
        return OfflineSigningCode.ACTION_EXPIRED
    if isinstance(action, PreparedRevokeAction):
        return _validate_revoke_action(action, expected_digest, now)
    tx = action.transaction
    try:
        route = transfer_route(action.network_id, action.asset_id)
    except Exception:
        return OfflineSigningCode.ACTION_INVALID
    expected_target = action.recipient if route.token_contract is None else route.token_contract
    expected_value = action.amount_atomic if route.token_contract is None else 0
    expected_calldata = (
        "0x"
        if route.token_contract is None
        else encode_usdc_transfer(action.recipient, action.amount_atomic)
    )
    valid = (
        action.schema_version == TRANSFER_SCHEMA_VERSION
        and action.digest == expected_digest
        and action.expires_at - action.created_at == ACTION_LIFETIME
        and action.profile_id != ""
        and Web3.is_checksum_address(action.sender)
        and Web3.is_checksum_address(action.recipient)
        and action.network_label == route.network_label
        and action.chain_id == route.chain_id
        and action.token == route.symbol
        and action.token_contract == route.token_contract
        and type(action.amount_atomic) is int
        and 0 < action.amount_atomic < 2**256
        and action.decimals == route.decimals
        and tx.transaction_type == 2
        and tx.chain_id == route.chain_id
        and tx.to.lower() == expected_target.lower()
        and tx.value == expected_value
        and tx.data == expected_calldata
        and tx.nonce >= 0
        and tx.gas > 0
        and tx.max_priority_fee_per_gas >= 0
        and tx.max_priority_fee_per_gas <= tx.max_fee_per_gas
        and tx.max_fee_per_gas > 0
        and action.max_total_fee_wei == tx.gas * tx.max_fee_per_gas
        and action.block_number >= 0
    )
    return None if valid else OfflineSigningCode.ACTION_INVALID


def offline_signing_result_to_map(
    result: OfflineSigningResult,
) -> dict[str, object]:
    title, message = _result_text(result.code)
    return {
        "success": result.success,
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
        "completedAt": result.completed_at,
        "simulation": result.simulation,
    }


def transaction_dict(action: PreparedTransactionAction) -> dict[str, object]:
    tx = action.transaction
    return {
        "type": 2,
        "chainId": tx.chain_id,
        "nonce": tx.nonce,
        "to": tx.to,
        "value": tx.value,
        "data": tx.data,
        "gas": tx.gas,
        "maxFeePerGas": tx.max_fee_per_gas,
        "maxPriorityFeePerGas": tx.max_priority_fee_per_gas,
        "accessList": [],
    }


def decoded_transaction_matches(
    decoded: Mapping[str, object], action: PreparedTransactionAction,
) -> bool:
    tx = action.transaction
    try:
        decoded_to = "0x" + bytes(decoded["to"]).hex()
        decoded_data = "0x" + bytes(decoded["data"]).hex()
        return (
            int(decoded["type"]) == 2
            and int(decoded["chainId"]) == tx.chain_id
            and int(decoded["nonce"]) == tx.nonce
            and decoded_to.lower() == tx.to.lower()
            and int(decoded["value"]) == tx.value
            and decoded_data.lower() == tx.data.lower()
            and int(decoded["gas"]) == tx.gas
            and int(decoded["maxFeePerGas"]) == tx.max_fee_per_gas
            and int(decoded["maxPriorityFeePerGas"])
            == tx.max_priority_fee_per_gas
            and not decoded["accessList"]
        )
    except (KeyError, TypeError, ValueError):
        return False


def _validate_revoke_action(
    action: PreparedRevokeAction,
    expected_digest: str,
    now: datetime,
) -> OfflineSigningCode | None:
    try:
        route = approval_route(action.network_id)
        expected_data = encode_usdc_approve_zero(action.spender)
    except Exception:
        return OfflineSigningCode.ACTION_INVALID
    tx = action.transaction
    valid = (
        action.schema_version == REVOKE_SCHEMA_VERSION
        and action.action_type == REVOKE_ACTION_TYPE
        and action.digest == expected_digest
        and action.expires_at - action.created_at == REVOKE_LIFETIME
        and now < action.expires_at
        and action.profile_id != ""
        and Web3.is_checksum_address(action.sender)
        and Web3.is_checksum_address(action.spender)
        and action.sender.lower() != action.spender.lower()
        and action.network_label == route.network_label
        and action.chain_id == route.chain_id
        and action.token == "USDC"
        and action.token_contract == route.token_contract
        and type(action.allowance_before_atomic) is int
        and 0 < action.allowance_before_atomic < 2**256
        and action.new_allowance_atomic == 0
        and action.decimals == 6
        and tx.transaction_type == 2
        and tx.chain_id == route.chain_id
        and tx.to.lower() == route.token_contract.lower()
        and tx.value == 0
        and tx.data == expected_data
        and tx.nonce >= 0
        and tx.gas > 0
        and 0 <= tx.max_priority_fee_per_gas <= tx.max_fee_per_gas
        and tx.max_fee_per_gas > 0
        and action.max_total_fee_wei == tx.gas * tx.max_fee_per_gas
        and action.block_number >= 0
    )
    return None if valid else OfflineSigningCode.ACTION_INVALID


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _short_hash(value: str) -> str:
    if not value:
        return ""
    return f"{value[:14]}…{value[-10:]}"


def _short_address(value: str) -> str:
    if not value:
        return ""
    return f"{value[:8]}…{value[-6:]}"


def _format_wei(value: int) -> str:
    rendered = format(Decimal(value).scaleb(-18), "f").rstrip("0").rstrip(".")
    return rendered or "0"


def _result_text(code: OfflineSigningCode) -> tuple[str, str]:
    return {
        OfflineSigningCode.SUCCESS: (
            "Transaction signed locally",
            "No transaction was broadcast. Raw signed data was discarded.",
        ),
        OfflineSigningCode.AUTHENTICATION_FAILED: (
            "Authentication failed",
            "Nothing was signed or sent. Prepare a new action to try again.",
        ),
        OfflineSigningCode.ACTION_EXPIRED: (
            "Preparation expired",
            "Nothing was signed or sent. Live transaction data must be prepared again.",
        ),
        OfflineSigningCode.ACTION_INVALID: (
            "Transaction changed",
            "Nothing was signed or sent. The prepared action is no longer valid.",
        ),
        OfflineSigningCode.POLICY_UNAVAILABLE: (
            "Signing unavailable",
            "The local maximum-fee limit is not configured.",
        ),
        OfflineSigningCode.FEE_LIMIT_EXCEEDED: (
            "Fee limit exceeded",
            "Nothing was signed or sent. Prepare a new action when fees are lower.",
        ),
        OfflineSigningCode.CANCELLED: (
            "Signing cancelled",
            "Nothing was signed or sent.",
        ),
        OfflineSigningCode.SIGNING_FAILED: (
            "Offline signing failed",
            "Nothing was signed or sent. Prepare a new action to try again.",
        ),
    }[code]
