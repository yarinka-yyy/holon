"""Strict request and safe-response payload validation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from .codes import RefusalCode
from .model import SCHEMA_VERSION, ActionState, MessageKind
from .schemas import PAYLOAD_FIELDS
from .violations import ContractViolation

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,77}$")
NON_NEGATIVE_RE = re.compile(r"^(?:0|[1-9][0-9]{0,77})$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
HUMAN_AMOUNT_RE = re.compile(r"^[0-9]+(?:[.,][0-9]+)?$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
FLOW_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
DANGEROUS_FIELDS = frozenset({"contract", "method", "selector", "calldata", "value"})
GUARD_STATES = frozenset(
    {"NORMAL", "ENTERING", "ACTIVE", "EXITING", "RECOVERY_REQUIRED", "SIGNING_DISABLED"}
)
BALANCE_STATUSES = frozenset({"READY", "PARTIAL", "DEGRADED"})
NETWORK_STATUSES = frozenset({"LIVE", "UNAVAILABLE"})
NETWORK_FIELDS = frozenset(
    {
        "network", "chain_id", "status", "block_number", "updated_at",
        "error_code", "balances",
    }
)
ASSET_FIELDS = frozenset({"asset", "amount_atomic", "decimals", "display"})
BALANCE_CODES = frozenset(
    {
        "BALANCES_READY",
        "BALANCES_PARTIAL",
        "BALANCES_UNAVAILABLE",
        "WALLET_NOT_CREATED",
        "WALLET_UNAVAILABLE",
    }
)
BALANCE_MESSAGES = {
    "BALANCES_READY": "Wallet balances are available.",
    "BALANCES_PARTIAL": "Some Wallet balances are unavailable.",
    "BALANCES_UNAVAILABLE": "Wallet balances are unavailable.",
    "WALLET_NOT_CREATED": "Wallet has not been created.",
    "WALLET_UNAVAILABLE": "Wallet public data is unavailable.",
}
BALANCE_ERROR_CODES = frozenset(
    {
        "ACCOUNT_CHANGED",
        "DATA_INVALID",
        "DATA_UNAVAILABLE",
        "RPC_TIMEOUT",
        "RPC_UNAVAILABLE",
        "TOKEN_METADATA_INVALID",
        "WALLET_NOT_CREATED",
        "WALLET_UNAVAILABLE",
        "WRONG_CHAIN",
    }
)
UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)


def _transfer(payload: Mapping[str, Any]) -> None:
    if DANGEROUS_FIELDS & set(payload):
        raise ContractViolation(RefusalCode.ARBITRARY_CALL_REFUSED.value, "Arbitrary calls are refused.")
    expected = PAYLOAD_FIELDS[MessageKind.PREPARE_TRANSFER]
    if "max_total_fee_wei" not in payload:
        raise ContractViolation(RefusalCode.MAX_FEE_REQUIRED.value, "Maximum fee is required.")
    if set(payload) != expected:
        code = RefusalCode.UNKNOWN_AUTHORITY_FIELD if set(payload) - expected else RefusalCode.REQUEST_INVALID
        raise ContractViolation(code.value, "Invalid authority fields.")
    if payload.get("action_type") != "transfer":
        raise ContractViolation(RefusalCode.ACTION_NOT_ALLOWED.value, "Action is not supported.")
    version = payload.get("policy_version")
    if not isinstance(version, str) or not version.isdigit() or len(version) > 8:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid policy version.")
    for field in ("network", "asset"):
        if not isinstance(payload.get(field), str) or NAME_RE.fullmatch(payload[field]) is None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid transfer field.")
    for field in ("amount_atomic", "max_total_fee_wei"):
        if not isinstance(payload.get(field), str) or DECIMAL_RE.fullmatch(payload[field]) is None:
            code = RefusalCode.MAX_FEE_REQUIRED if field.startswith("max_") else RefusalCode.AMOUNT_INVALID
            raise ContractViolation(code.value, "Invalid bounded amount.")
    recipient = payload.get("recipient")
    if not isinstance(recipient, str) or ADDRESS_RE.fullmatch(recipient) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid recipient.")


def _transfer_intent(payload: Mapping[str, Any]) -> None:
    expected = PAYLOAD_FIELDS[MessageKind.TRANSFER_INTENT]
    if DANGEROUS_FIELDS & set(payload):
        raise ContractViolation(
            RefusalCode.ARBITRARY_CALL_REFUSED.value, "Arbitrary calls are refused."
        )
    if set(payload) != expected:
        code = (
            RefusalCode.UNKNOWN_AUTHORITY_FIELD
            if set(payload) - expected
            else RefusalCode.REQUEST_INVALID
        )
        raise ContractViolation(code.value, "Invalid authority fields.")
    network = payload.get("network")
    asset = payload.get("asset")
    if network not in {"ethereum", "base"} or asset not in {"eth", "usdc"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid transfer route.")
    amount = payload.get("amount")
    decimals = 18 if asset == "eth" else 6
    if (
        not isinstance(amount, str)
        or len(amount) > 80
        or "." in amount and "," in amount
        or HUMAN_AMOUNT_RE.fullmatch(amount) is None
    ):
        raise ContractViolation(RefusalCode.AMOUNT_INVALID.value, "Invalid transfer amount.")
    normalized = amount.replace(",", ".")
    whole, separator, fraction = normalized.partition(".")
    if len(fraction) > decimals:
        raise ContractViolation(RefusalCode.AMOUNT_INVALID.value, "Invalid transfer amount.")
    atomic = int(whole) * 10**decimals
    if separator:
        atomic += int(fraction.ljust(decimals, "0"))
    if atomic <= 0 or atomic >= 2**256:
        raise ContractViolation(RefusalCode.AMOUNT_INVALID.value, "Invalid transfer amount.")
    recipient = payload.get("recipient")
    if not isinstance(recipient, str) or ADDRESS_RE.fullmatch(recipient) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid recipient.")


def _safe_text(payload: Mapping[str, Any]) -> None:
    code = payload.get("code")
    message = payload.get("message")
    if not isinstance(code, str) or CODE_RE.fullmatch(code) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid response code.")
    if not isinstance(message, str) or not message or len(message) > 256:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid response text.")


def _asset(value: object, symbol: str, decimals: int) -> None:
    if not isinstance(value, Mapping) or set(value) != ASSET_FIELDS:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance asset.")
    if value.get("asset") != symbol or value.get("decimals") != decimals:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance asset.")
    atomic = value.get("amount_atomic")
    display = value.get("display")
    if not isinstance(atomic, str) or NON_NEGATIVE_RE.fullmatch(atomic) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance amount.")
    if display != _display_units(int(atomic), decimals, symbol):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance display.")


def _display_units(atomic: int, decimals: int, symbol: str) -> str:
    if decimals > 6 and atomic and atomic < 10 ** (decimals - 6):
        return f"<0.000001 {symbol}"
    shown_decimals = min(decimals, 6)
    truncated = atomic // (10 ** (decimals - shown_decimals))
    scale = 10**shown_decimals
    whole, fraction = divmod(truncated, scale)
    suffix = f".{fraction:0{shown_decimals}d}".rstrip("0").rstrip(".")
    return f"{whole}{suffix} {symbol}"


def _network(value: object, network: str, chain_id: int) -> None:
    if not isinstance(value, Mapping) or set(value) != NETWORK_FIELDS:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid network balance.")
    if value.get("network") != network or value.get("chain_id") != chain_id:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid network balance.")
    status = value.get("status")
    if status not in NETWORK_STATUSES:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid network status.")
    block = value.get("block_number")
    updated = value.get("updated_at")
    error_code = value.get("error_code")
    balances = value.get("balances")
    if status == "UNAVAILABLE":
        if block is not None or updated is not None or balances is not None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable balance.")
        if error_code not in BALANCE_ERROR_CODES:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance error.")
        return
    if not isinstance(block, str) or NON_NEGATIVE_RE.fullmatch(block) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance block.")
    if not isinstance(updated, str) or UTC_TIMESTAMP_RE.fullmatch(updated) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance timestamp.")
    try:
        datetime.fromisoformat(updated.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ContractViolation(
            RefusalCode.REQUEST_INVALID.value, "Invalid balance timestamp."
        ) from exc
    if error_code is not None or not isinstance(balances, Mapping):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid live balance.")
    if set(balances) != {"ETH", "USDC"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance assets.")
    _asset(balances["ETH"], "ETH", 18)
    _asset(balances["USDC"], "USDC", 6)


def validate_wallet_balances(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.WALLET_BALANCES]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance payload.")
    _safe_text(payload)
    if payload.get("status") not in BALANCE_STATUSES:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance status.")
    if payload.get("code") not in BALANCE_CODES:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance code.")
    if payload.get("message") != BALANCE_MESSAGES[payload["code"]]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance message.")
    if payload.get("authority_available") is not False:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid authority status.")
    account = payload.get("account")
    if account is not None:
        if not isinstance(account, Mapping) or set(account) != {"label", "address"}:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid public Account.")
        label = account.get("label")
        if not isinstance(label, str) or not label or len(label) > 64 or any(ord(c) < 32 for c in label):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid public Account.")
        address = account.get("address")
        if not isinstance(address, str) or ADDRESS_RE.fullmatch(address) is None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid public Account.")
    networks = payload.get("networks")
    if not isinstance(networks, list) or len(networks) != 2:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance networks.")
    _network(networks[0], "ethereum", 1)
    _network(networks[1], "base", 8453)
    live = sum(item["status"] == "LIVE" for item in networks)
    expected_status = "READY" if live == 2 else "PARTIAL" if live == 1 else "DEGRADED"
    if payload.get("status") != expected_status or (account is None and live):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent balance status.")
    expected_code = {
        "READY": "BALANCES_READY",
        "PARTIAL": "BALANCES_PARTIAL",
    }.get(expected_status)
    if expected_code is not None and payload.get("code") != expected_code:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent balance code.")


def _response(kind: MessageKind, payload: Mapping[str, Any]) -> None:
    _safe_text(payload)
    if kind in {MessageKind.REFUSAL, MessageKind.ERROR}:
        if type(payload.get("retryable")) is not bool:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid retry status.")
        return
    state = payload.get("guard_state")
    if state not in GUARD_STATES:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Guard state.")
    if kind in {MessageKind.PROTECTED_FLOW_STARTED, MessageKind.ACTION_STATUS, MessageKind.RECOVERY_REQUIRED}:
        try:
            ActionState(payload.get("action_state"))
        except (TypeError, ValueError) as exc:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid action state.") from exc
        flow_id = payload.get("flow_id")
        if flow_id is not None and (not isinstance(flow_id, str) or FLOW_RE.fullmatch(flow_id) is None):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid flow identifier.")
    if "authority_available" in payload and type(payload["authority_available"]) is not bool:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid authority status.")
    if kind is MessageKind.HEALTH_RESPONSE and payload.get("compatibility") not in {"COMPATIBLE", "INCOMPATIBLE"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid compatibility status.")
    if kind is MessageKind.WALLET_OPENED and payload.get("wallet_state") not in {
        "OPENED", "ACTIVATED",
    }:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Wallet state.")
    if kind is MessageKind.WALLET_OPENED and payload.get("authority_available") is not False:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid authority status.")
    if kind is MessageKind.COMPATIBILITY_STATUS:
        if payload.get("supported_schema_versions") != [SCHEMA_VERSION]:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid schema list.")
        version = payload.get("policy_version")
        if not isinstance(version, str) or not version.isdigit():
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid policy version.")


def validate_payload(kind: MessageKind, payload: Mapping[str, Any]) -> None:
    if kind is MessageKind.TRANSFER_INTENT:
        _transfer_intent(payload)
        return
    if kind is MessageKind.PREPARE_TRANSFER:
        _transfer(payload)
        return
    if kind is MessageKind.WALLET_BALANCES:
        validate_wallet_balances(payload)
        return
    if set(payload) != PAYLOAD_FIELDS[kind]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid message payload.")
    if payload:
        _response(kind, payload)
