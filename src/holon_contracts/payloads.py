"""Strict request and safe-response payload validation."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Mapping

from holon_earn import EarnContractError, EarnPortfolioSnapshot

from .codes import RefusalCode
from .model import SCHEMA_VERSION, ActionState, MessageKind
from .registry import load_registry
from .schemas import PAYLOAD_FIELDS
from .violations import ContractViolation

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,77}$")
NON_NEGATIVE_RE = re.compile(r"^(?:0|[1-9][0-9]{0,77})$")
SIGNED_ATOMIC_RE = re.compile(r"^-?(?:0|[1-9][0-9]{0,77})$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
HUMAN_AMOUNT_RE = re.compile(r"^[0-9]+(?:[.,][0-9]+)?$")
CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
FLOW_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$")
DANGEROUS_FIELDS = frozenset({"contract", "method", "selector", "calldata", "value"})
GUARD_STATES = frozenset(
    {"NORMAL", "ENTERING", "ACTIVE", "EXITING", "RECOVERY_REQUIRED", "SIGNING_DISABLED"}
)
BALANCE_STATUSES = frozenset({"READY", "PARTIAL", "DEGRADED"})
NETWORK_STATUSES = frozenset({"LIVE", "PARTIAL", "UNAVAILABLE"})
NETWORK_FIELDS = frozenset(
    {
        "network", "chain_id", "status", "block_number", "updated_at",
        "error_code", "balances",
    }
)
ASSET_FIELDS = frozenset({"asset", "amount_atomic", "decimals", "display"})
ASSET_V2_FIELDS = frozenset(
    {"asset_id", "asset", "status", "amount_atomic", "decimals", "display", "error_code"}
)
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
        "RATE_LIMITED",
        "ASSET_DATA_UNAVAILABLE",
        "TOKEN_DATA_UNAVAILABLE",
        "TOKEN_METADATA_INVALID",
        "WALLET_NOT_CREATED",
        "WALLET_UNAVAILABLE",
        "WRONG_CHAIN",
    }
)
UTC_TIMESTAMP_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
LENDING_PERCENT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,6})?$")
LENDING_RAW_RE = re.compile(r"^(?:0|[1-9][0-9]{0,77})(?:\.[0-9]{1,36})?$")
LENDING_CONTRACTS = (
    ("aave-v3", "base-usdc", "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"),
    ("compound-v3", "base-usdc", "0xb125E6687d4313864e53df431d5425969c15Eb2F"),
    ("morpho-v1", "gauntlet-usdc-prime-v1", "0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61"),
)
LENDING_DISPLAY_NAMES = {
    "aave-v3": "Aave V3",
    "compound-v3": "Compound III",
    "morpho-v1": "Morpho Gauntlet USDC Prime",
}
LENDING_NETWORK = {"network": "base", "chain_id": 8453}
LENDING_ASSET = {
    "asset": "USDC", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "decimals": 6,
}
LENDING_CAVEATS = frozenset({
    "AAVE_DATA_UNAVAILABLE", "AAVE_POSITION_UNAVAILABLE", "BASE_RPC_UNAVAILABLE",
    "COMPOUND_DATA_UNAVAILABLE", "COMPOUND_POSITION_UNAVAILABLE",
    "INCENTIVES_NOT_PROFILED", "MORPHO_DATA_UNAVAILABLE",
    "MORPHO_POSITION_UNAVAILABLE", "READ_PROFILES_CORRUPT",
    "READ_PROFILES_INCOMPATIBLE", "READ_PROFILES_INTEGRITY_FAILED",
    "READ_PROFILES_MISSING", "READ_PROFILES_UNAVAILABLE",
    "WALLET_ACCOUNT_UNAVAILABLE",
})
LENDING_ACTION_CHECKS = frozenset({
    "ACTION_PROFILE_VERIFIED", "AAVE_ACCOUNT_DEBT_ZERO", "AAVE_IDENTITY_VERIFIED",
    "AAVE_LIQUIDITY_AVAILABLE", "AAVE_RESERVE_AVAILABLE",
    "AAVE_SUPPLY_CAP_AVAILABLE", "ALLOWANCE_EXACT", "ALLOWANCE_ZERO",
    "AUSDC_POSITION_AVAILABLE", "FEE_BALANCE_AVAILABLE",
    "SIMULATION_SUCCEEDED", "USDC_BALANCE_AVAILABLE",
    "PROTOCOL_IDENTITY_VERIFIED", "COMPOUND_ACTION_AVAILABLE",
    "COMPOUND_BORROW_ZERO", "MORPHO_VAULT_AVAILABLE",
    "MORPHO_INFLATION_PROTECTION_VERIFIED", "LENDING_POSITION_AVAILABLE",
})
LENDING_ACTION_CAVEATS = frozenset({
    "ACTION_PROFILES_CORRUPT", "ACTION_PROFILES_INCOMPATIBLE",
    "ACTION_PROFILES_INTEGRITY_FAILED", "ACTION_PROFILES_MISSING",
    "ACTION_PROFILE_MISMATCH",
    "ACTION_PROFILES_UNAVAILABLE", "ACCOUNT_CHANGED", "AAVE_ACCOUNT_HAS_DEBT",
    "AAVE_BLOCK_STALE", "AAVE_IDENTITY_MISMATCH", "AAVE_RESERVE_FROZEN",
    "AAVE_RESERVE_INACTIVE", "AAVE_RESERVE_PAUSED", "AAVE_SUPPLY_CAP_REACHED",
    "BASE_RPC_UNAVAILABLE", "FEE_NOT_POLICY_AUTHORIZED", "GAS_ESTIMATE_FAILED",
    "INSUFFICIENT_AUSDC", "INSUFFICIENT_ETH", "INSUFFICIENT_PROTOCOL_LIQUIDITY",
    "INSUFFICIENT_USDC", "LENDING_ACTION_INVALID", "PREVIEW_ONLY",
    "PROTECTED_FLOW_ACTIVE", "SIMULATION_FAILED", "UNEXPECTED_ALLOWANCE",
    "WALLET_ACCOUNT_UNAVAILABLE", "WALLET_UNAVAILABLE", "WRONG_CHAIN",
    "LENDING_BLOCK_STALE", "LENDING_IDENTITY_MISMATCH",
    "PROTOCOL_ACTION_PAUSED", "COMPOUND_ACCOUNT_HAS_BORROW",
    "MORPHO_INFLATION_PROTECTION_MISSING", "MORPHO_DEPOSIT_UNAVAILABLE",
    "INSUFFICIENT_LENDING_POSITION",
})
LENDING_WRITE_IDENTITIES = {
    "aave-v3-base-usdc": ("aave-v3", LENDING_CONTRACTS[0][2]),
    "compound-v3-base-usdc": ("compound-v3", LENDING_CONTRACTS[1][2]),
    "morpho-v1-gauntlet-usdc-prime": ("morpho-v1", LENDING_CONTRACTS[2][2]),
}
LENDING_ACTION_CODES = frozenset({
    "LENDING_ACTION_PREVIEW_READY", "LENDING_ACTION_REFUSED",
    "LENDING_ACTION_UNAVAILABLE",
})
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")
MODULE_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
MODULE_ACTION_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
ACTION_ID_RE = re.compile(
    r"^act-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
MODULE_FORBIDDEN_FIELDS = frozenset({
    "credential", "mnemonic", "password", "private", "secret", "seed", "signed",
})
MODULE_ACTION_FORBIDDEN_FIELDS = MODULE_FORBIDDEN_FIELDS | frozenset({
    "calldata", "contract", "method", "selector", "signature", "wire",
})


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


def validate_lending_action_intent(payload: Mapping[str, Any]) -> None:
    if DANGEROUS_FIELDS & set(payload):
        raise ContractViolation(
            RefusalCode.ARBITRARY_CALL_REFUSED.value, "Arbitrary calls are refused.",
        )
    if set(payload) != PAYLOAD_FIELDS[MessageKind.LENDING_ACTION_INTENT]:
        code = (
            RefusalCode.UNKNOWN_AUTHORITY_FIELD
            if set(payload) - PAYLOAD_FIELDS[MessageKind.LENDING_ACTION_INTENT]
            else RefusalCode.REQUEST_INVALID
        )
        raise ContractViolation(code.value, "Invalid Lending action fields.")
    fixed = {
        "module_id": "lending", "module_version": "1",
        "protocol_profile_version": "1", "network": "base", "asset": "usdc",
        "beneficiary_mode": "active_wallet_account",
    }
    if any(payload.get(field) != value for field, value in fixed.items()):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending action.")
    if payload.get("protocol_profile_id") not in {
        "aave-v3-base-usdc", "compound-v3-base-usdc",
        "morpho-v1-gauntlet-usdc-prime",
    }:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending profile.")
    action, mode, amount = payload.get("action"), payload.get("amount_mode"), payload.get("amount")
    if action not in {"supply", "withdraw"} or mode not in {"exact", "all"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending action.")
    if mode == "all":
        if amount is not None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending amount.")
        return
    if (
        not isinstance(amount, str) or len(amount) > 80
        or "." in amount and "," in amount or HUMAN_AMOUNT_RE.fullmatch(amount) is None
    ):
        raise ContractViolation(RefusalCode.AMOUNT_INVALID.value, "Invalid Lending amount.")
    whole, separator, fraction = amount.replace(",", ".").partition(".")
    atomic = int(whole) * 10**6 + (int(fraction.ljust(6, "0")) if separator and len(fraction) <= 6 else 0)
    if len(fraction) > 6 or atomic <= 0 or atomic >= 2**256:
        raise ContractViolation(RefusalCode.AMOUNT_INVALID.value, "Invalid Lending amount.")


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
    v1_fields = PAYLOAD_FIELDS[MessageKind.WALLET_BALANCES] - {"balance_schema_version"}
    if "balance_schema_version" not in payload:
        _validate_wallet_balances_v1(payload, v1_fields)
        return
    if set(payload) != PAYLOAD_FIELDS[MessageKind.WALLET_BALANCES] or payload.get("balance_schema_version") != "2":
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
    registry = load_registry()
    networks = payload.get("networks")
    if not isinstance(networks, list) or len(networks) != len(registry.networks):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance networks.")
    for value, spec in zip(networks, registry.networks, strict=True):
        _network_v2(value, spec.network_id, spec.chain_id)
    available = sum(item["status"] in {"LIVE", "PARTIAL"} for item in networks)
    expected_status = (
        "READY" if all(item["status"] == "LIVE" for item in networks)
        else "PARTIAL" if available else "DEGRADED"
    )
    if payload.get("status") != expected_status or (account is None and available):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent balance status.")
    expected_code = {
        "READY": "BALANCES_READY",
        "PARTIAL": "BALANCES_PARTIAL",
    }.get(expected_status)
    if expected_code is not None and payload.get("code") != expected_code:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent balance code.")


def _validate_wallet_balances_v1(
    payload: Mapping[str, Any], fields: frozenset[str],
) -> None:
    if set(payload) != fields:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance payload.")
    _safe_text(payload)
    if payload.get("status") not in BALANCE_STATUSES or payload.get("code") not in BALANCE_CODES:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance payload.")
    if payload.get("message") != BALANCE_MESSAGES[payload["code"]] or payload.get("authority_available") is not False:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance payload.")
    account = payload.get("account")
    if account is not None and (
        not isinstance(account, Mapping) or set(account) != {"label", "address"}
        or not isinstance(account.get("label"), str) or not account["label"]
        or not isinstance(account.get("address"), str) or ADDRESS_RE.fullmatch(account["address"]) is None
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid public Account.")
    networks = payload.get("networks")
    if not isinstance(networks, list) or len(networks) != 2:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance networks.")
    _network(networks[0], "ethereum", 1)
    _network(networks[1], "base", 8453)
    live = sum(item["status"] == "LIVE" for item in networks)
    expected = "READY" if live == 2 else "PARTIAL" if live == 1 else "DEGRADED"
    if payload.get("status") != expected or (account is None and live):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent balance status.")
    expected_code = {
        "READY": "BALANCES_READY",
        "PARTIAL": "BALANCES_PARTIAL",
    }.get(expected)
    if expected_code is not None and payload.get("code") != expected_code:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent balance code.")


def _network_v2(value: object, network_id: str, chain_id: int) -> None:
    if not isinstance(value, Mapping) or set(value) != NETWORK_FIELDS:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid network balance.")
    if value.get("network") != network_id or value.get("chain_id") != chain_id:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid network balance.")
    registry = load_registry()
    deployments = registry.deployments_by_network[network_id]
    balances = value.get("balances")
    if not isinstance(balances, list) or len(balances) != len(deployments):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance assets.")
    for asset, deployment in zip(balances, deployments, strict=True):
        _asset_v2(asset, deployment.asset_id)
    status = value.get("status")
    block, updated, error = value.get("block_number"), value.get("updated_at"), value.get("error_code")
    if status == "UNAVAILABLE":
        if block is not None or updated is not None or error not in BALANCE_ERROR_CODES:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable balance.")
        if any(item["status"] != "UNAVAILABLE" for item in balances):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable balance.")
        return
    if status not in {"LIVE", "PARTIAL"} or not isinstance(block, str) or NON_NEGATIVE_RE.fullmatch(block) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid live balance.")
    if not isinstance(updated, str) or UTC_TIMESTAMP_RE.fullmatch(updated) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance timestamp.")
    if status == "LIVE" and (error is not None or any(item["status"] != "LIVE" for item in balances)):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid live balance.")
    if status == "PARTIAL" and (error != "ASSET_DATA_UNAVAILABLE" or all(item["status"] == "LIVE" for item in balances)):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid partial balance.")


def _asset_v2(value: object, asset_id: str) -> None:
    if not isinstance(value, Mapping) or set(value) != ASSET_V2_FIELDS or value.get("asset_id") != asset_id:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance asset.")
    registry = load_registry()
    spec = registry.asset_by_id[asset_id]
    if value.get("asset") != spec.display_symbol or value.get("decimals") != spec.decimals:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance asset.")
    if value.get("status") == "UNAVAILABLE":
        if value.get("amount_atomic") is not None or value.get("display") is not None or value.get("error_code") not in BALANCE_ERROR_CODES:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable asset.")
        return
    atomic = value.get("amount_atomic")
    if value.get("status") != "LIVE" or value.get("error_code") is not None or not isinstance(atomic, str) or NON_NEGATIVE_RE.fullmatch(atomic) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance amount.")
    if value.get("display") != _display_units(int(atomic), spec.decimals, spec.display_symbol):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid balance display.")


def _lending_identity(payload: Mapping[str, Any]) -> None:
    if payload.get("network") != LENDING_NETWORK or payload.get("asset") != LENDING_ASSET:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending identity.")
    if payload.get("authority_available") is not False:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid authority status.")


def _lending_freshness(value: object) -> None:
    fields = {"state", "observed_at", "block_number"}
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending freshness.")
    state = value.get("state")
    observed, block = value.get("observed_at"), value.get("block_number")
    if state == "UNAVAILABLE":
        if observed is not None or block is not None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending freshness.")
        return
    if state not in {"LIVE", "STALE"} or not isinstance(observed, str) or UTC_TIMESTAMP_RE.fullmatch(observed) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending freshness.")
    if type(block) is not int or block <= 0:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending block.")
    try:
        datetime.fromisoformat(observed.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending timestamp.") from exc


def _lending_caveats(value: object) -> None:
    if (
        not isinstance(value, list) or len(value) > 4 or len(set(value)) != len(value)
        or any(item not in LENDING_CAVEATS for item in value)
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending caveats.")


def _lending_market(value: object, expected: tuple[str, str, str]) -> None:
    fields = {
        "protocol", "market_id", "contract_address", "base_yield", "incentives",
        "confirmed_total_annual_percent", "total_completeness", "freshness", "caveats",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending market.")
    if tuple(value[name] for name in ("protocol", "market_id", "contract_address")) != expected:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending market identity.")
    _lending_freshness(value["freshness"])
    _lending_caveats(value["caveats"])
    base = value["base_yield"]
    if value["freshness"]["state"] == "UNAVAILABLE":
        if base is not None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable rate.")
    else:
        base_fields = {
            "source_raw_value", "source_raw_unit", "value_percent", "metric",
            "comparison_apy_percent", "normalization",
        }
        if not isinstance(base, Mapping) or set(base) != base_fields:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending rate.")
        if (
            not isinstance(base["source_raw_value"], str)
            or LENDING_RAW_RE.fullmatch(base["source_raw_value"]) is None
            or base["source_raw_unit"] not in {"ray_apr", "per_second_wad", "decimal_fraction"}
            or base["metric"] not in {"APR", "APY"}
            or base["normalization"] not in {"per_second_compounding_365d", "reported_apy"}
            or any(not isinstance(base[name], str) or LENDING_PERCENT_RE.fullmatch(base[name]) is None
                   for name in ("value_percent", "comparison_apy_percent"))
        ):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending rate.")
    incentives = value["incentives"]
    if not isinstance(incentives, Mapping) or set(incentives) != {"status", "total_apr_percent", "components"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending incentives.")
    components = incentives["components"]
    if incentives["status"] == "UNAVAILABLE":
        if incentives["total_apr_percent"] is not None or components != []:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable incentives.")
    elif incentives["status"] == "AVAILABLE":
        total = incentives["total_apr_percent"]
        if not isinstance(total, str) or LENDING_PERCENT_RE.fullmatch(total) is None or not isinstance(components, list) or len(components) > 8:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending incentives.")
        for component in components:
            if not isinstance(component, Mapping) or set(component) != {"asset_address", "apr_percent"}:
                raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending reward.")
            if not isinstance(component["asset_address"], str) or ADDRESS_RE.fullmatch(component["asset_address"]) is None:
                raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending reward.")
            if not isinstance(component["apr_percent"], str) or LENDING_PERCENT_RE.fullmatch(component["apr_percent"]) is None:
                raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending reward.")
    else:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending incentives.")
    total = value["confirmed_total_annual_percent"]
    completeness = value["total_completeness"]
    if value["freshness"]["state"] == "UNAVAILABLE":
        if total is not None or completeness != "UNAVAILABLE":
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending total.")
    elif (
        not isinstance(total, str) or LENDING_PERCENT_RE.fullmatch(total) is None
        or completeness not in {"BASE_ONLY", "BASE_AND_INCENTIVES"}
        or (completeness == "BASE_AND_INCENTIVES") != (incentives["status"] == "AVAILABLE")
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending total.")


def validate_lending_markets(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.LENDING_MARKETS]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending payload.")
    _safe_text(payload)
    _lending_identity(payload)
    markets = payload.get("markets")
    if not isinstance(markets, list) or len(markets) != 3:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending markets.")
    for market, expected in zip(markets, LENDING_CONTRACTS, strict=True):
        _lending_market(market, expected)
    usable = [item for item in markets if item["freshness"]["state"] in {"LIVE", "STALE"}]
    live = sum(item["freshness"]["state"] == "LIVE" for item in markets)
    expected_status = "READY" if live == 3 else "PARTIAL" if usable else "DEGRADED"
    codes = {"READY": "LENDING_MARKETS_READY", "PARTIAL": "LENDING_MARKETS_PARTIAL", "DEGRADED": "LENDING_UNAVAILABLE"}
    messages = {
        "READY": "Lending markets are available.",
        "PARTIAL": "Some Lending markets are unavailable or stale.",
        "DEGRADED": "Lending data is unavailable.",
    }
    if payload.get("status") != expected_status or payload.get("code") != codes[expected_status] or payload.get("message") != messages[expected_status]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent Lending status.")
    highest = payload.get("highest_observed")
    if not usable:
        if highest is not None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending comparison.")
    elif not isinstance(highest, Mapping) or set(highest) != {"protocol", "comparison_apy_percent", "not_safety_recommendation"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending comparison.")
    elif (
        highest["protocol"] not in {item["protocol"] for item in usable}
        or not isinstance(highest["comparison_apy_percent"], str)
        or LENDING_PERCENT_RE.fullmatch(highest["comparison_apy_percent"]) is None
        or highest["not_safety_recommendation"] is not True
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending comparison.")
    recommendation = payload.get("recommendation")
    if not usable:
        if recommendation is not None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending recommendation.")
    elif not isinstance(recommendation, Mapping) or set(recommendation) != {
        "protocol", "confirmed_total_annual_percent", "missing_incentive_protocols",
        "incomplete_comparison", "requires_user_confirmation",
    }:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending recommendation.")
    else:
        missing = recommendation["missing_incentive_protocols"]
        expected_missing = [
            item["protocol"] for item in usable if item["total_completeness"] == "BASE_ONLY"
        ]
        if (
            recommendation["protocol"] not in {item["protocol"] for item in usable}
            or not isinstance(recommendation["confirmed_total_annual_percent"], str)
            or LENDING_PERCENT_RE.fullmatch(recommendation["confirmed_total_annual_percent"]) is None
            or missing != expected_missing
            or recommendation["incomplete_comparison"] is not (
                bool(missing) or len(usable) < len(markets)
            )
            or recommendation["requires_user_confirmation"] is not True
        ):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending recommendation.")
    delivery = payload.get("delivery")
    if not isinstance(delivery, Mapping) or set(delivery) != {
        "fetched_at", "cache_age_seconds", "cache_max_age_seconds", "force_refreshed",
    }:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending delivery.")
    if (
        not isinstance(delivery["fetched_at"], str)
        or UTC_TIMESTAMP_RE.fullmatch(delivery["fetched_at"]) is None
        or type(delivery["cache_age_seconds"]) is not int
        or not 0 <= delivery["cache_age_seconds"] <= 30
        or delivery["cache_max_age_seconds"] != 30
        or type(delivery["force_refreshed"]) is not bool
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending delivery.")


def _lending_position(value: object, expected: tuple[str, str, str]) -> None:
    fields = {
        "protocol", "market_id", "contract_address", "amount_atomic", "decimals",
        "display_amount", "freshness", "caveats",
    }
    if not isinstance(value, Mapping) or set(value) != fields:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending position.")
    if tuple(value[name] for name in ("protocol", "market_id", "contract_address")) != expected or value["decimals"] != 6:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending position identity.")
    _lending_freshness(value["freshness"])
    _lending_caveats(value["caveats"])
    atomic, display = value["amount_atomic"], value["display_amount"]
    if value["freshness"]["state"] == "UNAVAILABLE":
        if atomic is not None or display is not None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable position.")
    elif not isinstance(atomic, str) or NON_NEGATIVE_RE.fullmatch(atomic) is None or display != _display_units(int(atomic), 6, "USDC"):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending position amount.")


def validate_lending_positions(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.LENDING_POSITIONS]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending payload.")
    _safe_text(payload)
    _lending_identity(payload)
    account = payload.get("account")
    if account is not None and (
        not isinstance(account, Mapping) or set(account) != {"label", "address"}
        or not isinstance(account["label"], str) or not account["label"] or len(account["label"]) > 64
        or not isinstance(account["address"], str) or ADDRESS_RE.fullmatch(account["address"]) is None
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending Account.")
    positions = payload.get("positions")
    if not isinstance(positions, list) or len(positions) != 3:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending positions.")
    for position, expected in zip(positions, LENDING_CONTRACTS, strict=True):
        _lending_position(position, expected)
    usable = [item for item in positions if item["freshness"]["state"] in {"LIVE", "STALE"}]
    live = sum(item["freshness"]["state"] == "LIVE" for item in positions)
    expected_status = "READY" if live == 3 else "PARTIAL" if usable else "DEGRADED"
    expected = {
        "READY": ("LENDING_POSITIONS_READY", "Lending positions are available."),
        "PARTIAL": ("LENDING_POSITIONS_PARTIAL", "Some Lending positions are unavailable or stale."),
        "DEGRADED": ("LENDING_POSITIONS_UNAVAILABLE", "Lending positions are unavailable."),
    }[expected_status]
    if payload.get("status") != expected_status or (payload.get("code"), payload.get("message")) != expected:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent Lending status.")


def validate_lending_portfolio(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.LENDING_PORTFOLIO]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending portfolio.")
    _safe_text(payload)
    _lending_identity(payload)
    if payload.get("authority_available") is not False:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending authority status.")
    account = payload.get("account")
    if account is not None and (
        not isinstance(account, Mapping) or set(account) != {"label", "address"}
        or not isinstance(account["label"], str) or not account["label"]
        or not isinstance(account["address"], str) or ADDRESS_RE.fullmatch(account["address"]) is None
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending account.")
    summary = payload.get("summary")
    summary_fields = {
        "total_position_atomic", "display_total_position", "tracked_earnings_atomic",
        "display_tracked_earnings", "earnings_status",
        "weighted_confirmed_annual_percent", "yield_completeness",
    }
    if not isinstance(summary, Mapping) or set(summary) != summary_fields:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending summary.")
    _optional_atomic(summary["total_position_atomic"], signed=False)
    _optional_atomic(summary["tracked_earnings_atomic"], signed=True)
    total_position = summary["total_position_atomic"]
    if summary["display_total_position"] != (
        None if total_position is None else _display_units(int(total_position), 6, "USDC")
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending total display.")
    total_earnings = summary["tracked_earnings_atomic"]
    if summary["earnings_status"] == "AVAILABLE":
        if total_earnings is None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending total earnings.")
        amount = int(total_earnings)
        sign = "+" if amount > 0 else "−" if amount < 0 else ""
        if summary["display_tracked_earnings"] != f"{sign}{_display_units(abs(amount), 6, 'USDC')}":
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending total earnings display.")
    elif total_earnings is not None or summary["display_tracked_earnings"] is not None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable Lending total earnings.")
    weighted = summary["weighted_confirmed_annual_percent"]
    if weighted is not None and (
        not isinstance(weighted, str) or LENDING_PERCENT_RE.fullmatch(weighted) is None
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending weighted yield.")
    if summary["earnings_status"] not in {"AVAILABLE", "NOT_ENOUGH_HISTORY"} or summary["yield_completeness"] not in {"COMPLETE", "PARTIAL", "EMPTY"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending summary status.")
    completeness = summary["yield_completeness"]
    if (
        completeness == "EMPTY" and (total_position != "0" or weighted is not None)
        or completeness == "COMPLETE" and (
            total_position in {None, "0"} or weighted is None
        )
        or completeness == "PARTIAL" and weighted is not None
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent Lending summary yield.")
    protocols = payload.get("protocols")
    if not isinstance(protocols, list) or len(protocols) != 3:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending protocols.")
    for item, expected in zip(protocols, LENDING_CONTRACTS, strict=True):
        _lending_portfolio_protocol(item, expected)
    usable = [item for item in protocols if item["data_state"] != "UNAVAILABLE"]
    expected_status = (
        "READY" if all(item["data_state"] == "LIVE" for item in protocols)
        else "PARTIAL" if usable else "DEGRADED"
    )
    if payload.get("status") != expected_status:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent Lending portfolio status.")
    recommendation = payload.get("recommendation")
    if recommendation is not None:
        fields = {
            "protocol", "confirmed_total_annual_percent",
            "missing_incentive_protocols", "incomplete_comparison",
            "requires_user_confirmation",
        }
        missing = recommendation.get("missing_incentive_protocols") if isinstance(recommendation, Mapping) else None
        if (
            not isinstance(recommendation, Mapping) or set(recommendation) != fields
            or recommendation.get("protocol") not in {item["protocol"] for item in usable}
            or not isinstance(recommendation.get("confirmed_total_annual_percent"), str)
            or LENDING_PERCENT_RE.fullmatch(recommendation["confirmed_total_annual_percent"]) is None
            or not isinstance(missing, list)
            or any(not isinstance(item, str) for item in missing)
            or len(set(missing)) != len(missing)
            or any(item not in {protocol[0] for protocol in LENDING_CONTRACTS} for item in missing)
            or type(recommendation.get("incomplete_comparison")) is not bool
            or recommendation.get("requires_user_confirmation") is not True
        ):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending portfolio recommendation.")
    delivery = payload.get("delivery")
    if not isinstance(delivery, Mapping) or set(delivery) != {
        "fetched_at", "cache_age_seconds", "cache_max_age_seconds",
        "force_refreshed", "source",
    }:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending delivery.")
    fetched = delivery["fetched_at"]
    if fetched is not None and (not isinstance(fetched, str) or UTC_TIMESTAMP_RE.fullmatch(fetched) is None):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending delivery time.")
    if (
        type(delivery["cache_age_seconds"]) is not int or delivery["cache_age_seconds"] < 0
        or delivery["cache_max_age_seconds"] != 30
        or type(delivery["force_refreshed"]) is not bool
        or delivery["source"] not in {
            "LIVE_READ", "MEMORY_CACHE", "PERSISTED_FALLBACK", "UNAVAILABLE",
        }
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending delivery status.")
    history = payload.get("history")
    if (
        not isinstance(history, Mapping)
        or set(history) != {
            "period", "granularity", "period_start", "period_end", "points",
        }
        or history["period"] not in {"none", "7d", "30d", "all"}
        or history["granularity"] not in {"none", "day", "ten_day", "month"}
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending history.")
    for name in ("period_start", "period_end"):
        value = history[name]
        if value is not None and (
            not isinstance(value, str) or UTC_TIMESTAMP_RE.fullmatch(value) is None
        ):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending history range.")
    points = history["points"]
    if not isinstance(points, list) or len(points) > 12:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending history points.")
    if bool(points) != bool(history["period_start"] and history["period_end"]):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent Lending history range.")
    if (not points) != (history["granularity"] == "none"):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Inconsistent Lending history granularity.")
    for point in points:
        _lending_history_point(point)
    expected = {
        "READY": ("LENDING_PORTFOLIO_READY", "Lending portfolio is available."),
        "PARTIAL": ("LENDING_PORTFOLIO_PARTIAL", "Some Lending portfolio data is unavailable or cached."),
        "DEGRADED": ("LENDING_PORTFOLIO_UNAVAILABLE", "Lending portfolio is unavailable."),
    }.get(payload.get("status"))
    if expected is None or (payload.get("code"), payload.get("message")) != expected:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending portfolio status.")


def _optional_atomic(value: object, *, signed: bool) -> None:
    pattern = SIGNED_ATOMIC_RE if signed else NON_NEGATIVE_RE
    if value is not None and (not isinstance(value, str) or pattern.fullmatch(value) is None):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending amount.")


def _lending_portfolio_protocol(
    value: object, expected: tuple[str, str, str],
) -> None:
    fields = {
        "protocol", "market_id", "display_name", "contract_address",
        "position_atomic", "display_position", "base_yield", "incentives",
        "confirmed_total_annual_percent", "total_completeness",
        "tracked_earnings_atomic", "display_tracked_earnings", "earnings_status",
        "tracked_since", "data_state", "observed_at", "caveats",
    }
    if not isinstance(value, Mapping) or set(value) != fields or tuple(
        value[name] for name in ("protocol", "market_id", "contract_address")
    ) != expected:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending protocol.")
    if value["display_name"] != LENDING_DISPLAY_NAMES[expected[0]]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending display name.")
    _optional_atomic(value["position_atomic"], signed=False)
    _optional_atomic(value["tracked_earnings_atomic"], signed=True)
    position = value["position_atomic"]
    expected_position = None if position is None else _display_units(int(position), 6, "USDC")
    if value["display_position"] != expected_position:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending position display.")
    rate = value["confirmed_total_annual_percent"]
    if rate is not None and (not isinstance(rate, str) or LENDING_PERCENT_RE.fullmatch(rate) is None):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending rate.")
    if value["earnings_status"] not in {"AVAILABLE", "NOT_ENOUGH_HISTORY"} or value["data_state"] not in {"LIVE", "STALE", "CACHED", "UNAVAILABLE"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending protocol status.")
    completeness = value["total_completeness"]
    if completeness == "UNAVAILABLE":
        if rate is not None or value["base_yield"] is not None or value["incentives"] is not None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable Lending rate.")
    else:
        synthetic = {
            "protocol": expected[0], "market_id": expected[1],
            "contract_address": expected[2], "base_yield": value["base_yield"],
            "incentives": value["incentives"],
            "confirmed_total_annual_percent": rate,
            "total_completeness": completeness,
            "freshness": {
                "state": "LIVE", "observed_at": "2026-01-01T00:00:00Z",
                "block_number": 1,
            },
            "caveats": [],
        }
        _lending_market(synthetic, expected)
    earnings = value["tracked_earnings_atomic"]
    if value["earnings_status"] == "AVAILABLE":
        if earnings is None or value["tracked_since"] is None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending earnings.")
        amount = int(earnings)
        sign = "+" if amount > 0 else "−" if amount < 0 else ""
        expected_earnings = f"{sign}{_display_units(abs(amount), 6, 'USDC')}"
        if value["display_tracked_earnings"] != expected_earnings:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending earnings display.")
    elif any(value[name] is not None for name in (
        "tracked_earnings_atomic", "display_tracked_earnings", "tracked_since",
    )):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable Lending earnings.")
    for name in ("tracked_since", "observed_at"):
        timestamp = value[name]
        if timestamp is not None and (not isinstance(timestamp, str) or UTC_TIMESTAMP_RE.fullmatch(timestamp) is None):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending timestamp.")
    allowed_caveats = LENDING_CAVEATS | {
        "LENDING_PORTFOLIO_UNAVAILABLE", "USING_CACHED_DATA",
    }
    caveats = value["caveats"]
    if (
        not isinstance(caveats, list) or len(caveats) > 16
        or any(not isinstance(item, str) for item in caveats)
        or len(set(caveats)) != len(caveats)
        or any(item not in allowed_caveats for item in caveats)
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending caveats.")
    if value["data_state"] == "UNAVAILABLE":
        if position is not None or value["observed_at"] is not None or completeness != "UNAVAILABLE":
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable Lending protocol.")
    elif value["observed_at"] is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending observation time.")


def _lending_history_point(value: object) -> None:
    if not isinstance(value, Mapping) or set(value) != {
        "observed_at", "total_position_atomic", "tracked_earnings_atomic", "rates",
    }:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending history point.")
    if not isinstance(value["observed_at"], str) or UTC_TIMESTAMP_RE.fullmatch(value["observed_at"]) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending history time.")
    _optional_atomic(value["total_position_atomic"], signed=False)
    _optional_atomic(value["tracked_earnings_atomic"], signed=True)
    rates = value["rates"]
    if not isinstance(rates, Mapping) or set(rates) != {item[0] for item in LENDING_CONTRACTS}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending history rates.")
    for rate in rates.values():
        if rate is not None and (not isinstance(rate, str) or LENDING_PERCENT_RE.fullmatch(rate) is None):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending history rate.")


def _bounded_codes(value: object, allowed: frozenset[str], name: str) -> None:
    if (
        not isinstance(value, list) or len(value) > 16
        or len(set(value)) != len(value)
        or any(not isinstance(item, str) or item not in allowed for item in value)
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, f"Invalid {name}.")


def validate_lending_action_preview(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.LENDING_ACTION_PREVIEW]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending preview.")
    _safe_text(payload)
    status = payload.get("status")
    expected_code = {
        "PREVIEW_READY": "LENDING_ACTION_PREVIEW_READY",
        "REFUSED": "LENDING_ACTION_REFUSED",
        "UNAVAILABLE": "LENDING_ACTION_UNAVAILABLE",
    }.get(status)
    if expected_code is None or payload.get("code") != expected_code:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending preview status.")
    if payload.get("authority_available") is not False or payload.get("execution_available") is not False:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending authority status.")
    profile_id = payload.get("profile_id")
    identity = LENDING_WRITE_IDENTITIES.get(str(profile_id))
    if (
        identity is None or payload.get("protocol") != identity[0]
        or payload.get("profile_version") != "1"
        or payload.get("network") != {"network": "base", "chain_id": 8453}
        or payload.get("asset") != LENDING_ASSET
        or payload.get("native_value_wei") != "0"
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending preview identity.")
    requested = payload.get("requested_action")
    mode = payload.get("amount_mode")
    if requested is not None and requested not in {"supply", "withdraw"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending action.")
    if mode is not None and mode not in {"exact", "all"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending amount mode.")
    profile_digest = payload.get("profile_digest")
    if profile_digest is not None and (
        not isinstance(profile_digest, str) or HEX_64_RE.fullmatch(profile_digest) is None
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending profile digest.")
    account = payload.get("account")
    if account is not None and (
        not isinstance(account, Mapping) or set(account) != {"label", "address"}
        or not isinstance(account.get("label"), str) or not account["label"] or len(account["label"]) > 64
        or not isinstance(account.get("address"), str) or ADDRESS_RE.fullmatch(account["address"]) is None
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending Account.")
    _bounded_codes(payload.get("checks"), LENDING_ACTION_CHECKS, "Lending checks")
    _bounded_codes(payload.get("caveats"), LENDING_ACTION_CAVEATS, "Lending caveats")
    material = (
        "next_action", "amount_atomic", "call_amount_atomic", "display_amount", "target", "method",
        "calldata_hash", "nonce", "gas", "max_total_fee_wei", "block_number",
        "observed_at", "expires_at", "preview_digest",
        "position_before_atomic",
    )
    if status != "PREVIEW_READY":
        if any(payload.get(field) is not None for field in material) or payload.get("checks") != []:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable Lending preview.")
        if not payload.get("caveats"):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Missing Lending refusal reason.")
        return
    if account is None or requested is None or mode is None or profile_digest is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Incomplete Lending preview.")
    next_action = payload.get("next_action")
    if next_action not in {"approve", "supply", "deposit", "withdraw", "redeem"} or payload.get("method") != next_action:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending method.")
    if (
        requested == "withdraw" and next_action not in {"withdraw", "redeem"}
        or requested == "supply" and next_action not in {"approve", "supply", "deposit"}
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending action transition.")
    expected_target = (
        LENDING_ASSET["address"] if next_action == "approve" else identity[1]
    )
    if payload.get("target") != expected_target:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending target.")
    atomic = payload.get("amount_atomic")
    if not isinstance(atomic, str) or DECIMAL_RE.fullmatch(atomic) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending amount.")
    call_atomic = payload.get("call_amount_atomic")
    if not isinstance(call_atomic, str) or DECIMAL_RE.fullmatch(call_atomic) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending call amount.")
    position_before = payload.get("position_before_atomic")
    if (
        not isinstance(position_before, str)
        or not position_before.isdigit() or len(position_before) > 78
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending position.")
    if payload.get("display_amount") != _display_units(int(atomic), 6, "USDC"):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending display amount.")
    for field, pattern in (
        ("nonce", NON_NEGATIVE_RE), ("gas", DECIMAL_RE),
        ("max_total_fee_wei", DECIMAL_RE), ("block_number", DECIMAL_RE),
    ):
        value = payload.get(field)
        if not isinstance(value, str) or pattern.fullmatch(value) is None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending transaction field.")
    for field in ("calldata_hash", "preview_digest"):
        value = payload.get(field)
        if not isinstance(value, str) or HEX_64_RE.fullmatch(value) is None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending digest.")
    for field in ("observed_at", "expires_at"):
        value = payload.get(field)
        if not isinstance(value, str) or UTC_TIMESTAMP_RE.fullmatch(value) is None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending timestamp.")
        try:
            datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        except ValueError as exc:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending timestamp.") from exc


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


def _module_identifier(value: object, label: str) -> str:
    if (
        not isinstance(value, str)
        or len(value) > 64
        or MODULE_ID_RE.fullmatch(value) is None
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, f"Invalid {label}.")
    return value


def _module_json(value: object, *, depth: int = 0) -> None:
    if depth > 5:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Module data is too deep.")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Module data cannot use float.")
    if isinstance(value, str):
        if len(value) > 2048 or any(ord(character) < 32 for character in value):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module text.")
        return
    if isinstance(value, list):
        if len(value) > 64:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Module list is too large.")
        for item in value:
            _module_json(item, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Module object is too large.")
        for key, item in value.items():
            if (
                not isinstance(key, str)
                or not key
                or len(key) > 64
                or any(token in key.casefold() for token in MODULE_FORBIDDEN_FIELDS)
            ):
                raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module field.")
            _module_json(item, depth=depth + 1)
        return
    raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module data.")


def _module_action_json(value: object, *, depth: int = 0) -> None:
    """Validate semantic module action data and reject raw signing/wire fields."""
    _module_json(value, depth=depth)
    if isinstance(value, list):
        for item in value:
            _module_action_json(item, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if any(token in key.casefold() for token in MODULE_ACTION_FORBIDDEN_FIELDS):
                raise ContractViolation(
                    RefusalCode.ARBITRARY_CALL_REFUSED.value,
                    "Raw module actions and signing material are refused.",
                )
            _module_action_json(item, depth=depth + 1)


def validate_module_read_request(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.MODULE_READ_REQUEST]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module read request.")
    _module_identifier(payload.get("module_id"), "module id")
    _module_identifier(payload.get("capability_id"), "capability id")
    _module_identifier(payload.get("operation"), "module operation")
    params = payload.get("params")
    if not isinstance(params, Mapping):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module parameters.")
    if "active_account" in params:
        raise ContractViolation(
            RefusalCode.UNKNOWN_AUTHORITY_FIELD.value,
            "Active Wallet account is supplied only by Guard.",
        )
    _module_json(params)


def validate_module_read_response(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.MODULE_READ_RESPONSE]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module read response.")
    if payload.get("status") not in {"READY", "UNAVAILABLE"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module status.")
    _module_identifier(payload.get("module_id"), "module id")
    _module_identifier(payload.get("capability_id"), "capability id")
    _module_identifier(payload.get("operation"), "module operation")
    result = payload.get("result")
    if not isinstance(result, Mapping):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module result.")
    _module_json(result)
    code = payload.get("code")
    message = payload.get("message")
    if not isinstance(code, str) or CODE_RE.fullmatch(code) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module code.")
    if (
        not isinstance(message, str)
        or not message
        or len(message) > 256
        or any(ord(character) < 32 for character in message)
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module message.")


def validate_module_action_intent(
    payload: Mapping[str, Any], *, authority: bool,
) -> None:
    kind = (
        MessageKind.MODULE_AUTHORITY_INTENT
        if authority else MessageKind.MODULE_ACTION_INTENT
    )
    expected = PAYLOAD_FIELDS[kind]
    if set(payload) != expected:
        code = (
            RefusalCode.UNKNOWN_AUTHORITY_FIELD
            if set(payload) - expected else RefusalCode.REQUEST_INVALID
        )
        raise ContractViolation(code.value, "Invalid module action fields.")
    _module_identifier(payload.get("module_id"), "module id")
    _module_identifier(payload.get("capability_id"), "capability id")
    action_type = payload.get("action_type")
    if not isinstance(action_type, str) or MODULE_ACTION_RE.fullmatch(action_type) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action type.")
    params = payload.get("params")
    if not isinstance(params, Mapping):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action parameters.")
    _module_action_json(params)
    if authority:
        preview_digest = payload.get("preview_digest")
        if not isinstance(preview_digest, str) or HEX_64_RE.fullmatch(preview_digest) is None:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action digest.")


def validate_module_action_preview(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.MODULE_ACTION_PREVIEW]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action preview.")
    _safe_text(payload)
    status = payload.get("status")
    expected_code = {
        "PREVIEW_READY": "MODULE_ACTION_PREVIEW_READY",
        "REFUSED": "MODULE_ACTION_REFUSED",
        "UNAVAILABLE": "MODULE_ACTION_UNAVAILABLE",
    }.get(status)
    if expected_code is None or payload.get("code") != expected_code:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action status.")
    if (
        type(payload.get("authority_available")) is not bool
        or type(payload.get("execution_available")) is not bool
        or payload.get("authority_available") != payload.get("execution_available")
        or status != "PREVIEW_READY" and payload.get("authority_available") is not False
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module authority status.")
    _module_identifier(payload.get("module_id"), "module id")
    _module_identifier(payload.get("capability_id"), "capability id")
    action_type = payload.get("action_type")
    if not isinstance(action_type, str) or MODULE_ACTION_RE.fullmatch(action_type) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action type.")
    account = payload.get("account")
    if account is not None and (
        not isinstance(account, Mapping)
        or set(account) != {"address", "label"}
        or not isinstance(account.get("address"), str)
        or ADDRESS_RE.fullmatch(account["address"]) is None
        or not isinstance(account.get("label"), str)
        or not account["label"]
        or len(account["label"]) > 64
    ):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action account.")
    preview = payload.get("preview")
    if not isinstance(preview, Mapping):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action data.")
    _module_action_json(preview)
    checks = payload.get("checks")
    caveats = payload.get("caveats")
    for values, label in ((checks, "checks"), (caveats, "caveats")):
        if (
            not isinstance(values, list)
            or len(values) > 32
            or len(set(values)) != len(values)
            or any(not isinstance(item, str) or CODE_RE.fullmatch(item) is None for item in values)
        ):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, f"Invalid module action {label}.")
    preview_digest = payload.get("preview_digest")
    expires_at = payload.get("expires_at")
    if status == "PREVIEW_READY":
        if (
            account is None
            or not preview
            or not checks
            or not isinstance(preview_digest, str)
            or HEX_64_RE.fullmatch(preview_digest) is None
            or not isinstance(expires_at, str)
            or UTC_TIMESTAMP_RE.fullmatch(expires_at) is None
        ):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Incomplete module action preview.")
        return
    if account is not None or preview or checks or preview_digest is not None or expires_at is not None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid unavailable module action preview.")
    if not caveats:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Missing module action refusal reason.")


def validate_module_action_status_request(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.MODULE_ACTION_STATUS_REQUEST]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action status request.")
    _module_identifier(payload.get("module_id"), "module id")
    _module_identifier(payload.get("capability_id"), "capability id")


def validate_module_action_status(payload: Mapping[str, Any]) -> None:
    if set(payload) != PAYLOAD_FIELDS[MessageKind.MODULE_ACTION_STATUS]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action status.")
    _module_identifier(payload.get("module_id"), "module id")
    _module_identifier(payload.get("capability_id"), "capability id")
    action_type = payload.get("action_type")
    if not isinstance(action_type, str) or MODULE_ACTION_RE.fullmatch(action_type) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module action type.")
    operation_id = payload.get("operation_id")
    if not isinstance(operation_id, str) or ACTION_ID_RE.fullmatch(operation_id) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module operation id.")
    if payload.get("operation_state") not in {
        "PREPARED", "AWAITING_LOCAL_CONFIRMATION", "EXECUTING", "COMPLETED",
        "FAILED", "PARTIAL", "UNKNOWN", "REJECTED", "EXPIRED", "UNAVAILABLE",
    }:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module operation state.")
    phases = payload.get("phases")
    if not isinstance(phases, list) or len(phases) > 8:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid module phase states.")
    _module_action_json(phases)
    _safe_text(payload)


def validate_payload(kind: MessageKind, payload: Mapping[str, Any]) -> None:
    if kind is MessageKind.MODULE_READ_REQUEST:
        validate_module_read_request(payload)
        return
    if kind is MessageKind.MODULE_READ_RESPONSE:
        validate_module_read_response(payload)
        return
    if kind is MessageKind.MODULE_ACTION_INTENT:
        validate_module_action_intent(payload, authority=False)
        return
    if kind is MessageKind.MODULE_AUTHORITY_INTENT:
        validate_module_action_intent(payload, authority=True)
        return
    if kind is MessageKind.MODULE_ACTION_PREVIEW:
        validate_module_action_preview(payload)
        return
    if kind is MessageKind.MODULE_ACTION_STATUS_REQUEST:
        validate_module_action_status_request(payload)
        return
    if kind is MessageKind.MODULE_ACTION_STATUS:
        validate_module_action_status(payload)
        return
    if kind is MessageKind.READ_LENDING_MARKETS:
        if set(payload) not in (set(), {"force_refresh"}) or (
            "force_refresh" in payload and type(payload.get("force_refresh")) is not bool
        ):
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending refresh request.")
        return
    if kind is MessageKind.READ_LENDING_PORTFOLIO:
        if set(payload) - {"force_refresh", "history_period"}:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending portfolio request.")
        if "force_refresh" in payload and type(payload["force_refresh"]) is not bool:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending portfolio refresh.")
        if payload.get("history_period", "none") not in {"none", "7d", "30d", "all"}:
            raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending history period.")
        return
    if kind is MessageKind.READ_EARN_PORTFOLIO:
        if set(payload) - {"force_refresh"} or (
            "force_refresh" in payload and type(payload["force_refresh"]) is not bool
        ):
            raise ContractViolation(
                RefusalCode.REQUEST_INVALID.value, "Invalid Earn portfolio request.",
            )
        return
    if kind in {MessageKind.LENDING_ACTION_INTENT, MessageKind.LENDING_AUTHORITY_INTENT}:
        validate_lending_action_intent(payload)
        return
    if kind is MessageKind.TRANSFER_INTENT:
        _transfer_intent(payload)
        return
    if kind is MessageKind.PREPARE_TRANSFER:
        _transfer(payload)
        return
    if kind is MessageKind.WALLET_BALANCES:
        validate_wallet_balances(payload)
        return
    if kind is MessageKind.LENDING_MARKETS:
        validate_lending_markets(payload)
        return
    if kind is MessageKind.LENDING_POSITIONS:
        validate_lending_positions(payload)
        return
    if kind is MessageKind.LENDING_PORTFOLIO:
        validate_lending_portfolio(payload)
        return
    if kind is MessageKind.EARN_PORTFOLIO:
        try:
            EarnPortfolioSnapshot.from_dict(payload)
        except EarnContractError as exc:
            raise ContractViolation(
                RefusalCode.REQUEST_INVALID.value, "Invalid Earn portfolio.",
            ) from exc
        return
    if kind is MessageKind.LENDING_ACTION_PREVIEW:
        validate_lending_action_preview(payload)
        return
    if set(payload) != PAYLOAD_FIELDS[kind]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid message payload.")
    if payload:
        _response(kind, payload)
