"""Integrity-pinned Aave action shapes; never an authority policy."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from web3 import Web3

from .model import AAVE_CONTRACTS, BASE_CHAIN_ID, BASE_USDC, COMPOUND_CONTRACTS
from .morpho import MORPHO_VAULT_ADDRESS

ACTION_PROFILES_PATH = Path(__file__).with_name("action-profiles.json")
MAX_ACTION_PROFILES_BYTES = 32 * 1024
ACTION_PROFILES_DIGEST = "83640cdbbbbb8476eb1b34f0347430a5388a65034a63269f25e177c2671539be"
AAVE_SAFETY_PROFILE = {
    "allowed_intents": ["supply:exact", "supply:all", "withdraw:exact", "withdraw:all"],
    "allowed_methods": ["approve", "supply", "withdraw"],
    "beneficiary": "active_wallet_account",
    "native_value_wei": "0",
    "profile_id": "aave-v3-base-usdc",
    "safety_schema_version": "1",
}
AAVE_SAFETY_DIGEST = hashlib.sha256(
    json.dumps(AAVE_SAFETY_PROFILE, separators=(",", ":"), sort_keys=True).encode()
).hexdigest()
PROFILE_ID = "aave-v3-base-usdc"
PROFILE_VERSION = "1"
APPROVE_SELECTOR = "0x095ea7b3"
SUPPLY_SELECTOR = "0x617ba037"
WITHDRAW_SELECTOR = "0x69328dec"
COMPOUND_PROFILE_ID = "compound-v3-base-usdc"
MORPHO_PROFILE_ID = "morpho-v1-gauntlet-usdc-prime"


def _profile_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(dict(value), separators=(",", ":"), sort_keys=True).encode(),
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ProtocolActionProfile:
    profile_id: str
    profile_version: str
    protocol_id: str
    chain_id: int
    asset: str
    decimals: int
    target: str
    spender: str
    position_token: str
    digest: str
    safety_digest: str


def _additional_profile(
    profile_id: str, protocol_id: str, target: str, methods: list[str],
) -> ProtocolActionProfile:
    value = {
        "allowed_intents": ["supply:exact", "supply:all", "withdraw:exact", "withdraw:all"],
        "allowed_methods": ["approve", *methods], "asset": BASE_USDC,
        "beneficiary": "active_wallet_account", "chain_id": BASE_CHAIN_ID,
        "native_value_wei": "0",
        "profile_id": profile_id, "profile_version": "1", "protocol_id": protocol_id,
        "spender": target, "target": target,
    }
    digest = _profile_digest(value)
    safety = _profile_digest({**value, "safety_schema_version": "1"})
    return ProtocolActionProfile(
        profile_id, "1", protocol_id, BASE_CHAIN_ID, BASE_USDC, 6,
        target, target, target, digest, safety,
    )


COMPOUND_ACTION_PROFILE = _additional_profile(
    COMPOUND_PROFILE_ID, "compound-v3", COMPOUND_CONTRACTS["comet"],
    ["supply", "withdraw"],
)
MORPHO_ACTION_PROFILE = _additional_profile(
    MORPHO_PROFILE_ID, "morpho-v1", MORPHO_VAULT_ADDRESS,
    ["deposit", "withdraw", "redeem"],
)
ADDITIONAL_ACTION_PROFILES = (COMPOUND_ACTION_PROFILE, MORPHO_ACTION_PROFILE)
ACTION_PROFILE_DIGESTS = {
    PROFILE_ID: ACTION_PROFILES_DIGEST,
    **{profile.profile_id: profile.digest for profile in ADDITIONAL_ACTION_PROFILES},
}


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

    @property
    def protocol_id(self) -> str:
        return "aave-v3"

    @property
    def target(self) -> str:
        return self.pool

    @property
    def spender(self) -> str:
        return self.pool

    @property
    def position_token(self) -> str:
        return self.a_token

    @property
    def safety_digest(self) -> str:
        return AAVE_SAFETY_DIGEST

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

    def select(self, profile_id: str) -> AaveActionProfile | ProtocolActionProfile | None:
        if self.profile is not None and profile_id == self.profile.profile_id:
            return self.profile
        return next(
            (profile for profile in ADDITIONAL_ACTION_PROFILES if profile.profile_id == profile_id),
            None,
        )

    def select_by_digest(self, digest: str) -> AaveActionProfile | ProtocolActionProfile | None:
        return next((profile for profile in self.profiles if profile.digest == digest), None)

    @property
    def profiles(self) -> tuple[AaveActionProfile | ProtocolActionProfile, ...]:
        if self.profile is None:
            return ()
        return (self.profile, *ADDITIONAL_ACTION_PROFILES)

    @classmethod
    def load(cls, path: Path = ACTION_PROFILES_PATH) -> "ActionProfilesState":
        try:
            return cls("READY", load_action_profile(path), None)
        except ActionProfilesLoadError as exc:
            return cls("UNAVAILABLE", None, exc.code)
