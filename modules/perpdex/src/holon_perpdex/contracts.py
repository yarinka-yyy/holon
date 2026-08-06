"""Strict secret-free semantic contracts for protected PerpDEX actions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
import hashlib
import json
import re
from typing import Any

from .profile import (
    HLP_REVIEW_SECONDS,
    MARKET_REVIEW_SECONDS,
    MAX_HLP_DEPOSIT_USDC,
    MAX_LEVERAGE,
    MAX_OPEN_NOTIONAL_USDC,
    MIN_LEVERAGE,
    PROFILE_DIGEST,
    PROFILE_ID,
    PROFILE_VERSION,
    SUPPORTED_MARKETS,
)

CONTRACT_VERSION = "1"
_DECIMAL_RE = re.compile(r"^(?:0|[1-9][0-9]*)(?:\.[0-9]+)?$")
_ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_PHASE_ID_RE = re.compile(r"^phase-[0-9a-f]{32}$")
_CLOID_RE = re.compile(r"^0x[0-9a-f]{32}$")
_UTC_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}(?:\.[0-9]{1,6})?Z$"
)
_SENSITIVE = frozenset({"calldata", "credential", "password", "private_key", "secret", "seed", "signature", "signed_payload"})


class ContractError(ValueError):
    """A semantic PerpDEX value is unsafe or ambiguous."""


class ActionType(str, Enum):
    OPEN_POSITION = "OPEN_POSITION"
    CLOSE_POSITION = "CLOSE_POSITION"
    HLP_DEPOSIT = "HLP_DEPOSIT"
    HLP_WITHDRAW = "HLP_WITHDRAW"


class PositionSide(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


class AmountMode(str, Enum):
    FULL = "FULL"
    PERCENT = "PERCENT"
    EXACT = "EXACT"
    ALL = "ALL"


class PhaseType(str, Enum):
    SET_REFERRER = "SET_REFERRER"
    SET_ISOLATED_LEVERAGE = "SET_ISOLATED_LEVERAGE"
    CANCEL_MARKET_ORDERS = "CANCEL_MARKET_ORDERS"
    PLACE_IOC_ORDER = "PLACE_IOC_ORDER"
    VAULT_TRANSFER = "VAULT_TRANSFER"


def _decimal(value: object, label: str, *, scale: int = 18) -> Decimal:
    if not isinstance(value, str) or len(value) > 80 or _DECIMAL_RE.fullmatch(value) is None:
        raise ContractError(f"Invalid {label}")
    fraction = value.partition(".")[2]
    if len(fraction) > scale:
        raise ContractError(f"Invalid {label}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise ContractError(f"Invalid {label}") from exc
    if not parsed.is_finite():
        raise ContractError(f"Invalid {label}")
    return parsed


def _exact(value: Mapping[str, object], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise ContractError(f"Invalid {label} fields")


def _timestamp(value: object, label: str) -> str:
    if not isinstance(value, str) or _UTC_RE.fullmatch(value) is None:
        raise ContractError(f"Invalid {label}")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ContractError(f"Invalid {label}") from exc
    if parsed.tzinfo != UTC:
        raise ContractError(f"Invalid {label}")
    return value


def _json_safe(value: object, *, depth: int = 0) -> None:
    if depth > 8:
        raise ContractError("Protected action data is too deep")
    if value is None or isinstance(value, (bool, int, str)):
        if isinstance(value, str) and (len(value) > 2048 or any(ord(ch) < 32 for ch in value)):
            raise ContractError("Invalid protected action text")
        return
    if isinstance(value, float):
        raise ContractError("Floating-point protected action data is forbidden")
    if isinstance(value, list):
        if len(value) > 64:
            raise ContractError("Protected action list is too large")
        for item in value:
            _json_safe(item, depth=depth + 1)
        return
    if isinstance(value, Mapping):
        if len(value) > 64:
            raise ContractError("Protected action object is too large")
        for key, item in value.items():
            if not isinstance(key, str) or not key or key.casefold() in _SENSITIVE:
                raise ContractError("Invalid protected action field")
            _json_safe(item, depth=depth + 1)
        return
    raise ContractError("Invalid protected action data")


def canonical_json(value: object) -> bytes:
    _json_safe(value)
    return (json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")


def digest_json(value: object) -> str:
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class PerpDexActionIntent:
    action_type: ActionType
    market: str | None = None
    side: PositionSide | None = None
    notional_usdc: str | None = None
    leverage: int | None = None
    amount_mode: AmountMode | None = None
    percent: str | None = None
    amount_usdc: str | None = None

    @classmethod
    def from_mapping(cls, action_type: object, value: Mapping[str, object]) -> "PerpDexActionIntent":
        try:
            action = ActionType(action_type)
        except (TypeError, ValueError) as exc:
            raise ContractError("Invalid action type") from exc
        if not isinstance(value, Mapping):
            raise ContractError("Invalid action parameters")
        if action is ActionType.OPEN_POSITION:
            _exact(value, {"leverage", "market", "notional_usdc", "side"}, "open position")
            market = value["market"]
            if market not in SUPPORTED_MARKETS:
                raise ContractError("Unsupported market")
            try:
                side = PositionSide(value["side"])
            except (TypeError, ValueError) as exc:
                raise ContractError("Invalid position side") from exc
            amount = _decimal(value["notional_usdc"], "open notional", scale=6)
            leverage = value["leverage"]
            if amount <= 0 or amount > Decimal(MAX_OPEN_NOTIONAL_USDC):
                raise ContractError("Open notional exceeds the Holon bound")
            if type(leverage) is not int or not MIN_LEVERAGE <= leverage <= MAX_LEVERAGE:
                raise ContractError("Invalid leverage")
            return cls(action, str(market), side, str(value["notional_usdc"]), leverage)
        if action is ActionType.CLOSE_POSITION:
            _exact(value, {"amount_mode", "market", "percent"}, "close position")
            market = value["market"]
            if market not in SUPPORTED_MARKETS:
                raise ContractError("Unsupported market")
            try:
                mode = AmountMode(value["amount_mode"])
            except (TypeError, ValueError) as exc:
                raise ContractError("Invalid close amount mode") from exc
            if mode is AmountMode.FULL and value["percent"] is None:
                return cls(action, str(market), amount_mode=mode)
            if mode is not AmountMode.PERCENT:
                raise ContractError("Invalid close amount mode")
            percent = _decimal(value["percent"], "close percent", scale=6)
            if percent <= 0 or percent >= 100:
                raise ContractError("Invalid close percent")
            return cls(action, str(market), amount_mode=mode, percent=str(value["percent"]))
        if action is ActionType.HLP_DEPOSIT:
            _exact(value, {"amount_usdc"}, "HLP deposit")
            amount = _decimal(value["amount_usdc"], "HLP deposit amount", scale=6)
            if amount <= 0 or amount > Decimal(MAX_HLP_DEPOSIT_USDC):
                raise ContractError("HLP deposit exceeds the Holon bound")
            return cls(action, amount_usdc=str(value["amount_usdc"]))
        _exact(value, {"amount_mode", "amount_usdc"}, "HLP withdrawal")
        try:
            mode = AmountMode(value["amount_mode"])
        except (TypeError, ValueError) as exc:
            raise ContractError("Invalid HLP withdrawal mode") from exc
        if mode is AmountMode.ALL and value["amount_usdc"] is None:
            return cls(action, amount_mode=mode)
        if mode is not AmountMode.EXACT:
            raise ContractError("Invalid HLP withdrawal mode")
        amount = _decimal(value["amount_usdc"], "HLP withdrawal amount", scale=6)
        if amount <= 0:
            raise ContractError("Invalid HLP withdrawal amount")
        return cls(action, amount_mode=mode, amount_usdc=str(value["amount_usdc"]))

    @property
    def review_seconds(self) -> int:
        return MARKET_REVIEW_SECONDS if self.action_type in {
            ActionType.OPEN_POSITION, ActionType.CLOSE_POSITION,
        } else HLP_REVIEW_SECONDS

    @property
    def is_entry(self) -> bool:
        return self.action_type in {ActionType.OPEN_POSITION, ActionType.HLP_DEPOSIT}

    def to_mapping(self) -> dict[str, object]:
        if self.action_type is ActionType.OPEN_POSITION:
            return {"leverage": self.leverage, "market": self.market, "notional_usdc": self.notional_usdc, "side": self.side.value if self.side else None}
        if self.action_type is ActionType.CLOSE_POSITION:
            return {"amount_mode": self.amount_mode.value if self.amount_mode else None, "market": self.market, "percent": self.percent}
        if self.action_type is ActionType.HLP_DEPOSIT:
            return {"amount_usdc": self.amount_usdc}
        return {"amount_mode": self.amount_mode.value if self.amount_mode else None, "amount_usdc": self.amount_usdc}


@dataclass(frozen=True, slots=True)
class ProtectedActionPhase:
    phase_id: str
    phase_type: PhaseType
    nonce: str
    expires_at: str
    semantic: Mapping[str, object]
    wire_digest: str
    cloid: str | None = None

    def __post_init__(self) -> None:
        if _PHASE_ID_RE.fullmatch(self.phase_id) is None:
            raise ContractError("Invalid phase id")
        if not isinstance(self.nonce, str) or not self.nonce.isdigit() or len(self.nonce) > 20:
            raise ContractError("Invalid phase nonce")
        _timestamp(self.expires_at, "phase expiry")
        if _DIGEST_RE.fullmatch(self.wire_digest) is None:
            raise ContractError("Invalid wire digest")
        if self.cloid is not None and _CLOID_RE.fullmatch(self.cloid) is None:
            raise ContractError("Invalid client order id")
        _json_safe(self.semantic)

    def to_mapping(self) -> dict[str, object]:
        return {"cloid": self.cloid, "expires_at": self.expires_at, "nonce": self.nonce, "phase_id": self.phase_id, "phase_type": self.phase_type.value, "semantic": dict(self.semantic), "wire_digest": self.wire_digest}


@dataclass(frozen=True, slots=True)
class ProtectedActionBundle:
    operation_id: str
    account: str
    intent: PerpDexActionIntent
    snapshot_digest: str
    created_at: str
    expires_at: str
    phases: tuple[ProtectedActionPhase, ...]
    disclosure: str | None
    bundle_digest: str
    profile_id: str = PROFILE_ID
    profile_version: str = PROFILE_VERSION
    profile_digest: str = PROFILE_DIGEST
    bundle_version: str = CONTRACT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.operation_id, str) or not self.operation_id.startswith("act-"):
            raise ContractError("Invalid operation id")
        if _ADDRESS_RE.fullmatch(self.account) is None:
            raise ContractError("Invalid operation account")
        if self.profile_id != PROFILE_ID or self.profile_version != PROFILE_VERSION or self.profile_digest != PROFILE_DIGEST:
            raise ContractError("Protected action profile mismatch")
        if _DIGEST_RE.fullmatch(self.snapshot_digest) is None or _DIGEST_RE.fullmatch(self.bundle_digest) is None:
            raise ContractError("Invalid protected action digest")
        _timestamp(self.created_at, "bundle creation time")
        _timestamp(self.expires_at, "bundle expiry")
        if not self.phases or len(self.phases) > 8 or len({phase.phase_id for phase in self.phases}) != len(self.phases):
            raise ContractError("Invalid protected action phases")
        if self.disclosure is not None and (not self.disclosure or len(self.disclosure) > 512):
            raise ContractError("Invalid protected action disclosure")

    def material_mapping(self) -> dict[str, object]:
        return {
            "account": self.account, "bundle_version": self.bundle_version,
            "created_at": self.created_at, "disclosure": self.disclosure,
            "expires_at": self.expires_at, "intent": self.intent.to_mapping(),
            "operation_id": self.operation_id, "phases": [phase.to_mapping() for phase in self.phases],
            "profile_digest": self.profile_digest, "profile_id": self.profile_id,
            "profile_version": self.profile_version, "snapshot_digest": self.snapshot_digest,
        }

    def to_mapping(self) -> dict[str, object]:
        return {**self.material_mapping(), "bundle_digest": self.bundle_digest}

    def validate_digest(self) -> None:
        if digest_json(self.material_mapping()) != self.bundle_digest:
            raise ContractError("Protected action bundle digest mismatch")


@dataclass(frozen=True, slots=True)
class PerpDexActionPreview:
    status: str
    action_type: ActionType
    account: Mapping[str, str] | None
    preview: Mapping[str, object]
    preview_digest: str | None
    expires_at: str | None
    checks: tuple[str, ...]
    caveats: tuple[str, ...]
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class PerpDexActionResult:
    operation_id: str
    status: str
    action_type: ActionType
    phase_states: tuple[Mapping[str, object], ...]
    code: str
    message: str

    def __post_init__(self) -> None:
        if not self.operation_id.startswith("act-") or self.status not in {"COMPLETED", "FAILED", "PARTIAL", "UNKNOWN"}:
            raise ContractError("Invalid PerpDEX action result")
        for state in self.phase_states:
            _json_safe(state)
