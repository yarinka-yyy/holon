"""Integrity-pinned Aave action shapes; never an authority policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from web3 import Web3

from .model import AAVE_CONTRACTS, BASE_CHAIN_ID, BASE_USDC

ACTION_PROFILES_PATH = Path(__file__).with_name("action-profiles.json")
MAX_ACTION_PROFILES_BYTES = 32 * 1024
ACTION_PROFILES_DIGEST = "83640cdbbbbb8476eb1b34f0347430a5388a65034a63269f25e177c2671539be"
PROFILE_ID = "aave-v3-base-usdc"
PROFILE_VERSION = "1"
APPROVE_SELECTOR = "0x095ea7b3"
SUPPLY_SELECTOR = "0x617ba037"
WITHDRAW_SELECTOR = "0x69328dec"


class ActionProfilesValidationError(ValueError):
    def __init__(self, message: str, *, incompatible: bool = False) -> None:
        super().__init__(message)
        self.incompatible = incompatible


class ActionProfilesLoadError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Lending action profiles are unavailable")
        self.code = code


def canonical_action_profiles_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8") + b"\n"


def action_profiles_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_action_profiles_bytes(value)).hexdigest()


def _address(value: object, name: str) -> str:
    if not isinstance(value, str) or not Web3.is_checksum_address(value):
        raise ActionProfilesValidationError(f"{name} is invalid")
    return value


@dataclass(frozen=True, slots=True)
class AaveActionProfile:
    profile_id: str
    profile_version: str
    chain_id: int
    asset: str
    decimals: int
    pool: str
    provider: str
    data_provider: str
    a_token: str
    digest: str

    @classmethod
    def from_dict(cls, value: object, digest: str) -> "AaveActionProfile":
        if not isinstance(value, dict):
            raise ActionProfilesValidationError("Action profile must be an object")
        expected_root = {
            "asset", "fixed_parameters", "network", "profile_id", "profile_version",
            "protocol", "schema_version", "sources", "verified_at", "write_methods",
        }
        if set(value) != expected_root:
            raise ActionProfilesValidationError("Action profile fields are invalid")
        if value.get("schema_version") != "1" or value.get("profile_version") != "1":
            raise ActionProfilesValidationError(
                "Action profile version is incompatible", incompatible=True,
            )
        if value.get("profile_id") != PROFILE_ID or value.get("verified_at") != "2026-07-25":
            raise ActionProfilesValidationError("Action profile identity changed")
        if value.get("network") != {"chain_id": BASE_CHAIN_ID, "network_id": "base"}:
            raise ActionProfilesValidationError("Action network changed")
        asset = value.get("asset")
        if not isinstance(asset, dict) or set(asset) != {"address", "asset_id", "decimals"}:
            raise ActionProfilesValidationError("Action asset fields are invalid")
        _address(asset.get("address"), "USDC")
        if asset != {"address": BASE_USDC, "asset_id": "usdc", "decimals": 6}:
            raise ActionProfilesValidationError("Action asset changed")
        protocol = value.get("protocol")
        expected_protocol = {
            "a_token": AAVE_CONTRACTS["a_token"],
            "pool": AAVE_CONTRACTS["pool"],
            "pool_addresses_provider": AAVE_CONTRACTS["pool_addresses_provider"],
            "protocol_data_provider": AAVE_CONTRACTS["protocol_data_provider"],
            "protocol_id": "aave-v3",
        }
        if not isinstance(protocol, dict) or set(protocol) != set(expected_protocol):
            raise ActionProfilesValidationError("Aave contract fields are invalid")
        for name in expected_protocol:
            if name != "protocol_id":
                _address(protocol.get(name), name)
        if protocol != expected_protocol:
            raise ActionProfilesValidationError("Aave contract identity changed")
        if value.get("fixed_parameters") != {
            "beneficiary": "active_wallet_account", "native_value_wei": "0",
            "referral_code": 0,
        }:
            raise ActionProfilesValidationError("Fixed action parameters changed")
        expected_methods = [
            {"amount": "exact", "contract": "usdc", "method": "approve", "selector": APPROVE_SELECTOR, "spender": "pool"},
            {"asset": "usdc", "beneficiary": "active_wallet_account", "contract": "pool", "method": "supply", "referral_code": 0, "selector": SUPPLY_SELECTOR},
            {"amount": "exact_or_all", "asset": "usdc", "beneficiary": "active_wallet_account", "contract": "pool", "method": "withdraw", "selector": WITHDRAW_SELECTOR},
        ]
        if value.get("write_methods") != expected_methods:
            raise ActionProfilesValidationError("Allowed write methods changed")
        sources = value.get("sources")
        if not isinstance(sources, list) or len(sources) != 3:
            raise ActionProfilesValidationError("Action sources are invalid")
        for source in sources:
            if not isinstance(source, dict) or set(source) != {"revision", "source_id", "url"}:
                raise ActionProfilesValidationError("Action source fields are invalid")
            if not all(isinstance(source[field], str) and source[field] for field in source):
                raise ActionProfilesValidationError("Action source is invalid")
        return cls(
            PROFILE_ID, PROFILE_VERSION, BASE_CHAIN_ID, BASE_USDC, 6,
            protocol["pool"], protocol["pool_addresses_provider"],
            protocol["protocol_data_provider"], protocol["a_token"], digest,
        )


def load_action_profile(path: Path = ACTION_PROFILES_PATH) -> AaveActionProfile:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ActionProfilesLoadError("ACTION_PROFILES_MISSING") from exc
    try:
        if len(raw) > MAX_ACTION_PROFILES_BYTES:
            raise ActionProfilesValidationError("Action profiles are oversized")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ActionProfilesValidationError("Action profiles must be an object")
        canonical = canonical_action_profiles_bytes(value)
        if raw not in (canonical, canonical[:-1] + b"\r\n"):
            raise ActionProfilesValidationError("Action profiles are not canonical")
    except (UnicodeDecodeError, json.JSONDecodeError, ActionProfilesValidationError) as exc:
        incompatible = isinstance(exc, ActionProfilesValidationError) and exc.incompatible
        raise ActionProfilesLoadError(
            "ACTION_PROFILES_INCOMPATIBLE" if incompatible else "ACTION_PROFILES_CORRUPT",
        ) from exc
    if value.get("schema_version") != "1" or value.get("profile_version") != "1":
        raise ActionProfilesLoadError("ACTION_PROFILES_INCOMPATIBLE")
    digest = hashlib.sha256(canonical).hexdigest()
    if digest != ACTION_PROFILES_DIGEST:
        raise ActionProfilesLoadError("ACTION_PROFILES_INTEGRITY_FAILED")
    try:
        return AaveActionProfile.from_dict(value, digest)
    except ActionProfilesValidationError as exc:
        code = "ACTION_PROFILES_INCOMPATIBLE" if exc.incompatible else "ACTION_PROFILES_CORRUPT"
        raise ActionProfilesLoadError(code) from exc


@dataclass(frozen=True, slots=True)
class ActionProfilesState:
    status: str
    profile: AaveActionProfile | None
    error_code: str | None

    @classmethod
    def load(cls, path: Path = ACTION_PROFILES_PATH) -> "ActionProfilesState":
        try:
            return cls("READY", load_action_profile(path), None)
        except ActionProfilesLoadError as exc:
            return cls("UNAVAILABLE", None, exc.code)
