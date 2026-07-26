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
LENDING_PERCENT_RE = re.compile(r"^(?:0|[1-9][0-9]{0,3})(?:\.[0-9]{1,6})?$")
LENDING_RAW_RE = re.compile(r"^(?:0|[1-9][0-9]{0,77})(?:\.[0-9]{1,36})?$")
LENDING_CONTRACTS = (
    ("aave-v3", "base-usdc", "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"),
    ("compound-v3", "base-usdc", "0xb125E6687d4313864e53df431d5425969c15Eb2F"),
    ("morpho-v1", "gauntlet-usdc-prime-v1", "0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61"),
)
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
})
LENDING_ACTION_CODES = frozenset({
    "LENDING_ACTION_PREVIEW_READY", "LENDING_ACTION_REFUSED",
    "LENDING_ACTION_UNAVAILABLE",
})
HEX_64_RE = re.compile(r"^[0-9a-f]{64}$")


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
        "protocol_profile_id": "aave-v3-base-usdc",
        "protocol_profile_version": "1", "network": "base", "asset": "usdc",
        "beneficiary_mode": "active_wallet_account",
    }
    if any(payload.get(field) != value for field, value in fixed.items()):
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending action.")
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
        "freshness", "caveats",
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
    if (
        payload.get("protocol") != "aave-v3"
        or payload.get("profile_id") != "aave-v3-base-usdc"
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
        "next_action", "amount_atomic", "display_amount", "target", "method",
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
    if next_action not in {"approve", "supply", "withdraw"} or payload.get("method") != next_action:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending method.")
    if requested == "withdraw" and next_action != "withdraw" or requested == "supply" and next_action not in {"approve", "supply"}:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending action transition.")
    expected_target = (
        LENDING_ASSET["address"] if next_action == "approve" else LENDING_CONTRACTS[0][2]
    )
    if payload.get("target") != expected_target:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending target.")
    atomic = payload.get("amount_atomic")
    if not isinstance(atomic, str) or DECIMAL_RE.fullmatch(atomic) is None:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid Lending amount.")
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


def validate_payload(kind: MessageKind, payload: Mapping[str, Any]) -> None:
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
    if kind is MessageKind.LENDING_ACTION_PREVIEW:
        validate_lending_action_preview(payload)
        return
    if set(payload) != PAYLOAD_FIELDS[kind]:
        raise ContractViolation(RefusalCode.REQUEST_INVALID.value, "Invalid message payload.")
    if payload:
        _response(kind, payload)
