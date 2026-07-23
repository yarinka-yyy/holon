"""Versioned, public-only Wallet-initiated history persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Mapping

from .public_data import NETWORK_BY_ID
from .storage import StorageError, WalletPaths, atomic_write_json, read_json

HISTORY_SCHEMA_VERSION = 2
LEGACY_HISTORY_SCHEMA_VERSION = 1
MAX_HISTORY_RECORDS = 500
MONTH_LABELS = (
    "Jan", "Feb", "Mar", "Apr", "May", "Jun",
    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec",
)
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
HASH_RE = re.compile(r"^0x[0-9A-Fa-f]{64}$")
DECIMAL_RE = re.compile(r"^(0|[1-9][0-9]{0,77})$")


class HistoryStatus(str, Enum):
    PREPARED = "prepared"
    PENDING = "pending"
    CONFIRMED = "confirmed"
    FAILED = "failed"
    UNKNOWN = "unknown"


_TRANSITIONS = {
    HistoryStatus.PREPARED: {
        HistoryStatus.PREPARED,
        HistoryStatus.PENDING,
        HistoryStatus.FAILED,
        HistoryStatus.UNKNOWN,
    },
    HistoryStatus.PENDING: {
        HistoryStatus.PENDING,
        HistoryStatus.CONFIRMED,
        HistoryStatus.FAILED,
        HistoryStatus.UNKNOWN,
    },
    HistoryStatus.UNKNOWN: {
        HistoryStatus.UNKNOWN,
        HistoryStatus.PENDING,
        HistoryStatus.CONFIRMED,
        HistoryStatus.FAILED,
    },
    HistoryStatus.CONFIRMED: {HistoryStatus.CONFIRMED},
    HistoryStatus.FAILED: {HistoryStatus.FAILED},
}


class HistoryUnavailableError(RuntimeError):
    """The history file cannot be trusted or read."""


class HistoryValidationError(ValueError):
    """A history record or transition is invalid."""


@dataclass(frozen=True, slots=True)
class WalletHistoryRecord:
    action_id: str
    profile_id: str
    action_type: str
    network: str
    chain_id: int
    sender: str
    recipient: str
    contract: str | None
    token: str
    amount_atomic: str
    decimals: int
    transaction_hash: str | None
    status: HistoryStatus
    created_at: str
    updated_at: str
    simulated: bool
    max_total_fee_wei: str | None = None
    actual_fee_wei: str | None = None

    def __post_init__(self) -> None:
        _validate_record(self)

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "profile_id": self.profile_id,
            "action_type": self.action_type,
            "network": self.network,
            "chain_id": self.chain_id,
            "sender": self.sender,
            "recipient": self.recipient,
            "contract": self.contract,
            "token": self.token,
            "amount_atomic": self.amount_atomic,
            "decimals": self.decimals,
            "transaction_hash": self.transaction_hash,
            "status": self.status.value,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "simulated": self.simulated,
            "max_total_fee_wei": self.max_total_fee_wei,
            "actual_fee_wei": self.actual_fee_wei,
        }

    @classmethod
    def from_dict(
        cls, value: Mapping[str, Any], schema_version: int = HISTORY_SCHEMA_VERSION,
    ) -> WalletHistoryRecord:
        legacy_fields = {
            "action_id", "profile_id", "action_type", "network", "chain_id",
            "sender", "recipient", "contract", "token", "amount_atomic", "decimals",
            "transaction_hash", "status", "created_at", "updated_at", "simulated",
        }
        expected = (
            legacy_fields
            if schema_version == LEGACY_HISTORY_SCHEMA_VERSION
            else legacy_fields | {"max_total_fee_wei", "actual_fee_wei"}
        )
        if set(value) != expected:
            raise HistoryValidationError("History record fields are invalid")
        try:
            status = HistoryStatus(value["status"])
        except (TypeError, ValueError) as error:
            raise HistoryValidationError("History status is invalid") from error
        try:
            return cls(
                action_id=value["action_id"],
                profile_id=value["profile_id"],
                action_type=value["action_type"],
                network=value["network"],
                chain_id=value["chain_id"],
                sender=value["sender"],
                recipient=value["recipient"],
                contract=value["contract"],
                token=value["token"],
                amount_atomic=value["amount_atomic"],
                decimals=value["decimals"],
                transaction_hash=value["transaction_hash"],
                status=status,
                created_at=value["created_at"],
                updated_at=value["updated_at"],
                simulated=value["simulated"],
                max_total_fee_wei=value.get("max_total_fee_wei"),
                actual_fee_wei=value.get("actual_fee_wei"),
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, HistoryValidationError):
                raise
            raise HistoryValidationError("History record is invalid") from error


class HistoryStore:
    def __init__(self, paths: WalletPaths) -> None:
        self._path = paths.history

    @property
    def path(self):
        return self._path

    def load(self) -> tuple[WalletHistoryRecord, ...]:
        if not self._path.exists():
            return ()
        try:
            value = read_json(self._path)
            if not isinstance(value, dict) or set(value) != {"schema_version", "records"}:
                raise HistoryValidationError("History envelope is invalid")
            schema_version = value["schema_version"]
            if schema_version not in {
                LEGACY_HISTORY_SCHEMA_VERSION, HISTORY_SCHEMA_VERSION,
            }:
                raise HistoryValidationError("History schema is unsupported")
            records = value["records"]
            if not isinstance(records, list) or len(records) > MAX_HISTORY_RECORDS:
                raise HistoryValidationError("History records are invalid")
            parsed = tuple(
                WalletHistoryRecord.from_dict(item, schema_version) for item in records
            )
            if len({record.action_id for record in parsed}) != len(parsed):
                raise HistoryValidationError("History action IDs must be unique")
            return parsed
        except (StorageError, HistoryValidationError, TypeError) as error:
            raise HistoryUnavailableError("Wallet history is unavailable") from error

    def append(self, record: WalletHistoryRecord) -> tuple[WalletHistoryRecord, ...]:
        records = list(self.load())
        if any(existing.action_id == record.action_id for existing in records):
            raise HistoryValidationError("History action already exists")
        records.append(record)
        records = records[-MAX_HISTORY_RECORDS:]
        self._save(records)
        return tuple(records)

    def update_status(
        self,
        action_id: str,
        status: HistoryStatus,
        updated_at: str,
        transaction_hash: str | None = None,
        actual_fee_wei: str | None = None,
    ) -> tuple[WalletHistoryRecord, ...]:
        records = list(self.load())
        for index, record in enumerate(records):
            if record.action_id != action_id:
                continue
            if status not in _TRANSITIONS[record.status]:
                raise HistoryValidationError("History status transition is invalid")
            records[index] = replace(
                record,
                status=status,
                updated_at=updated_at,
                transaction_hash=(
                    transaction_hash if transaction_hash is not None
                    else record.transaction_hash
                ),
                actual_fee_wei=(
                    actual_fee_wei if actual_fee_wei is not None
                    else record.actual_fee_wei
                ),
            )
            self._save(records)
            return tuple(records)
        raise HistoryValidationError("History action is unknown")

    def _save(self, records: list[WalletHistoryRecord]) -> None:
        atomic_write_json(
            self._path,
            {
                "schema_version": HISTORY_SCHEMA_VERSION,
                "records": [record.to_dict() for record in records],
            },
        )


def history_record_to_map(record: WalletHistoryRecord) -> dict[str, object]:
    amount = _format_amount(record.amount_atomic, record.decimals, record.token)
    is_revoke = record.action_type == "revoke"
    return {
        "actionId": record.action_id,
        "profileId": record.profile_id,
        "actionType": record.action_type,
        "network": record.network,
        "networkLabel": "Ethereum" if record.network == "ethereum" else "Base",
        "chainId": record.chain_id,
        "sender": record.sender,
        "recipient": record.recipient,
        "shortRecipient": f"{record.recipient[:8]}…{record.recipient[-6:]}",
        "contract": record.contract or "",
        "token": record.token,
        "amount": amount,
        "transactionHash": record.transaction_hash or "",
        "status": record.status.value,
        "statusLabel": record.status.value.capitalize(),
        "createdAt": record.created_at,
        "updatedAt": record.updated_at,
        "dateLabel": _date_label(record.created_at),
        "simulated": record.simulated,
        "shortSender": f"{record.sender[:8]}…{record.sender[-6:]}",
        "maxTotalFeeWei": record.max_total_fee_wei or "",
        "actualFeeWei": record.actual_fee_wei or "",
        "maxFeeDisplay": _format_fee(record.max_total_fee_wei, maximum=True),
        "actualFeeDisplay": _format_fee(record.actual_fee_wei, maximum=False),
        "isRevoke": is_revoke,
        "summaryTitle": "Revoked USDC" if is_revoke else f"Sent {record.token}",
        "counterpartyLabel": "Spender" if is_revoke else "To",
        "amountLabel": "Allowance → 0" if is_revoke else f"−{amount}",
    }


def _validate_record(record: WalletHistoryRecord) -> None:
    if not isinstance(record.action_id, str) or not 1 <= len(record.action_id) <= 128:
        raise HistoryValidationError("History action ID is invalid")
    if not isinstance(record.profile_id, str) or not 1 <= len(record.profile_id) <= 128:
        raise HistoryValidationError("History profile ID is invalid")
    if record.action_type not in {"transfer", "revoke"}:
        raise HistoryValidationError("History action type is invalid")
    expected_chain = {"ethereum": 1, "base": 8453}.get(record.network)
    if expected_chain is None or type(record.chain_id) is not int or record.chain_id != expected_chain:
        raise HistoryValidationError("History network is invalid")
    if not _address(record.sender) or not _address(record.recipient):
        raise HistoryValidationError("History address is invalid")
    if record.contract is not None and not _address(record.contract):
        raise HistoryValidationError("History contract is invalid")
    if record.token not in {"ETH", "USDC"}:
        raise HistoryValidationError("History token is invalid")
    if not isinstance(record.amount_atomic, str) or DECIMAL_RE.fullmatch(record.amount_atomic) is None:
        raise HistoryValidationError("History amount is invalid")
    expected_decimals = 18 if record.token == "ETH" else 6
    if type(record.decimals) is not int or record.decimals != expected_decimals:
        raise HistoryValidationError("History decimals are invalid")
    if record.action_type == "revoke" and (
        record.token != "USDC"
        or record.amount_atomic != "0"
        or record.contract != NETWORK_BY_ID[record.network].usdc_contract
        or record.recipient.lower() in {
            record.sender.lower(),
            record.contract.lower(),
            "0x" + "00" * 20,
        }
    ):
        raise HistoryValidationError("History revoke is invalid")
    if record.transaction_hash is not None and (
        not isinstance(record.transaction_hash, str)
        or HASH_RE.fullmatch(record.transaction_hash) is None
    ):
        raise HistoryValidationError("History transaction hash is invalid")
    if type(record.simulated) is not bool:
        raise HistoryValidationError("History simulation marker is invalid")
    for fee in (record.max_total_fee_wei, record.actual_fee_wei):
        if fee is not None and (
            not isinstance(fee, str) or DECIMAL_RE.fullmatch(fee) is None
        ):
            raise HistoryValidationError("History fee is invalid")
    _parse_timestamp(record.created_at)
    _parse_timestamp(record.updated_at)


def _address(value: object) -> bool:
    return isinstance(value, str) and ADDRESS_RE.fullmatch(value) is not None


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise HistoryValidationError("History timestamp is invalid")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as error:
        raise HistoryValidationError("History timestamp is invalid") from error
    return parsed


def _format_amount(atomic: str, decimals: int, token: str) -> str:
    value = int(atomic)
    whole, fraction = divmod(value, 10 ** decimals)
    if not fraction:
        return f"{whole} {token}"
    digits = f"{fraction:0{decimals}d}".rstrip("0")
    return f"{whole}.{digits} {token}"


def _date_label(timestamp: str) -> str:
    parsed = _parse_timestamp(timestamp)
    return f"{MONTH_LABELS[parsed.month - 1]} {parsed.day}, {parsed.year}"


def _format_fee(value: str | None, *, maximum: bool) -> str:
    if value is None:
        return "Unavailable"
    atomic = int(value)
    whole, fraction = divmod(atomic, 10**18)
    rendered = str(whole)
    if fraction:
        rendered += "." + f"{fraction:018d}".rstrip("0")
    prefix = "≤ " if maximum else ""
    return f"{prefix}{rendered} ETH"
