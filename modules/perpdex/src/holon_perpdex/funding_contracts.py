"""Strict semantic bundle for the one supported Hyperliquid funding route."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
import re

from .contracts import (
    ActionType, ContractError, PerpDexActionIntent, PhaseType,
    ProtectedActionPhase, digest_json,
)
from .funding_profile import (
    ACTION_TYPE, ARBITRUM_CHAIN_ID, BRIDGE2_ADDRESS, MIN_AMOUNT_ATOMIC,
    NATIVE_USDC, PROFILE_DIGEST, PROFILE_ID, PROFILE_VERSION, REVIEW_SECONDS,
)

CONTRACT_VERSION = "1"
_ACTION_RE = re.compile(
    r"^act-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True, slots=True)
class FundingBundle:
    operation_id: str
    account: str
    intent: PerpDexActionIntent
    snapshot_digest: str
    created_at: str
    expires_at: str
    phases: tuple[ProtectedActionPhase, ...]
    bundle_digest: str
    disclosure: str = (
        "This sends native USDC on Arbitrum to the official Hyperliquid Bridge2. "
        "Only deposits of at least 5 USDC are credited; wrong assets or networks can be lost."
    )
    profile_id: str = PROFILE_ID
    profile_version: str = PROFILE_VERSION
    profile_digest: str = PROFILE_DIGEST
    bundle_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if (
            _ACTION_RE.fullmatch(self.operation_id) is None
            or _ADDRESS_RE.fullmatch(self.account) is None
            or self.intent.action_type is not ActionType.FUND_TRADING_ACCOUNT
            or self.profile_id != PROFILE_ID or self.profile_version != PROFILE_VERSION
            or self.profile_digest != PROFILE_DIGEST or self.bundle_version != CONTRACT_VERSION
            or _DIGEST_RE.fullmatch(self.snapshot_digest) is None
            or _DIGEST_RE.fullmatch(self.bundle_digest) is None
            or len(self.phases) != 1
        ):
            raise ContractError("Invalid funding bundle")
        created = _time(self.created_at)
        expires = _time(self.expires_at)
        if expires <= created or (expires - created).total_seconds() != REVIEW_SECONDS:
            raise ContractError("Invalid funding review lifetime")
        phase = self.phases[0]
        value = phase.semantic
        if (
            phase.phase_type is not PhaseType.ARBITRUM_USDC_TRANSFER
            or phase.expires_at != self.expires_at
            or value["bridge_address"].lower() != BRIDGE2_ADDRESS.lower()
            or value["token_contract"].lower() != NATIVE_USDC.lower()
            or value["chain_id"] != ARBITRUM_CHAIN_ID
            or value["amount_usdc"] != self.intent.amount_usdc
            or int(value["usd_atomic"]) < MIN_AMOUNT_ATOMIC
            or Decimal(value["amount_usdc"]) * Decimal(1_000_000) != Decimal(value["usd_atomic"])
        ):
            raise ContractError("Invalid funding route")

    def material_mapping(self) -> dict[str, object]:
        return {
            "account": self.account, "action_type": ACTION_TYPE,
            "bundle_version": self.bundle_version, "created_at": self.created_at,
            "disclosure": self.disclosure, "expires_at": self.expires_at,
            "intent": self.intent.to_mapping(), "operation_id": self.operation_id,
            "phases": [item.to_mapping() for item in self.phases],
            "profile_digest": self.profile_digest, "profile_id": self.profile_id,
            "profile_version": self.profile_version, "snapshot_digest": self.snapshot_digest,
        }

    def to_mapping(self) -> dict[str, object]:
        return {**self.material_mapping(), "bundle_digest": self.bundle_digest}

    def validate_digest(self) -> None:
        if digest_json(self.material_mapping()) != self.bundle_digest:
            raise ContractError("Funding bundle digest mismatch")

    @classmethod
    def from_mapping(cls, value: Mapping[str, object]) -> "FundingBundle":
        required = {
            "account", "action_type", "bundle_digest", "bundle_version", "created_at",
            "disclosure", "expires_at", "intent", "operation_id", "phases",
            "profile_digest", "profile_id", "profile_version", "snapshot_digest",
        }
        if not isinstance(value, Mapping) or set(value) != required:
            raise ContractError("Invalid funding bundle fields")
        if value["action_type"] != ACTION_TYPE or not isinstance(value["intent"], Mapping):
            raise ContractError("Invalid funding bundle action")
        if not isinstance(value["phases"], list) or len(value["phases"]) != 1:
            raise ContractError("Invalid funding phases")
        text = {
            "account", "bundle_digest", "bundle_version", "created_at", "disclosure",
            "expires_at", "operation_id", "profile_digest", "profile_id",
            "profile_version", "snapshot_digest",
        }
        if any(not isinstance(value[name], str) for name in text):
            raise ContractError("Invalid funding text")
        bundle = cls(
            str(value["operation_id"]), str(value["account"]),
            PerpDexActionIntent.from_mapping(ACTION_TYPE, value["intent"]),
            str(value["snapshot_digest"]), str(value["created_at"]),
            str(value["expires_at"]),
            (ProtectedActionPhase.from_mapping(value["phases"][0]),),
            str(value["bundle_digest"]), str(value["disclosure"]),
            str(value["profile_id"]), str(value["profile_version"]),
            str(value["profile_digest"]), str(value["bundle_version"]),
        )
        bundle.validate_digest()
        return bundle


def _time(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except (AttributeError, ValueError) as exc:
        raise ContractError("Invalid funding time") from exc
    if parsed.tzinfo != UTC:
        raise ContractError("Invalid funding time")
    return parsed
