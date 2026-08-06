"""Strict, secret-free normalized Earn contracts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Mapping

EARN_SCHEMA_VERSION = "1"
RISK_LIMITATION = "Risk methodology has not been approved for Holon Earn."
MAX_PRODUCTS = 128

_ID_RE = re.compile(r"[a-z0-9][a-z0-9._:-]{0,127}")
_NETWORK_RE = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")
_DECIMAL_RE = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?")
_UTC_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z")
_ADDRESS_RE = re.compile(r"0x[0-9A-Fa-f]{40}")


class EarnContractError(ValueError):
    """An Earn value is unsafe, ambiguous, or outside the fixed schema."""


class ProductCategory(str, Enum):
    LENDING = "LENDING"
    VAULT = "VAULT"


class MetricKind(str, Enum):
    SUPPLY_APY = "SUPPLY_APY"
    PROTOCOL_APR = "PROTOCOL_APR"
    TRAILING_RETURN = "TRAILING_RETURN"


class FreshnessState(str, Enum):
    LIVE = "LIVE"
    STALE = "STALE"
    CACHED = "CACHED"
    UNAVAILABLE = "UNAVAILABLE"


class AvailabilityState(str, Enum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class ProviderState(str, Enum):
    READY = "READY"
    DEGRADED = "DEGRADED"


class ProviderSource(str, Enum):
    LIVE = "LIVE"
    CACHED = "CACHED"
    UNAVAILABLE = "UNAVAILABLE"


class PortfolioState(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    DEGRADED = "DEGRADED"


def _plain_text(value: object, label: str, maximum: int = 256) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise EarnContractError(f"Invalid {label}")
    return value


def _identifier(value: object, label: str) -> str:
    text = _plain_text(value, label, 128)
    if _ID_RE.fullmatch(text) is None:
        raise EarnContractError(f"Invalid {label}")
    return text


def _network(value: object) -> str:
    text = _plain_text(value, "network id", 64)
    if _NETWORK_RE.fullmatch(text) is None:
        raise EarnContractError("Invalid network id")
    return text


def decimal_string(value: object, label: str, *, signed: bool = False) -> str:
    if not isinstance(value, str) or _DECIMAL_RE.fullmatch(value) is None:
        raise EarnContractError(f"Invalid {label}")
    try:
        parsed = Decimal(value)
    except InvalidOperation as exc:
        raise EarnContractError(f"Invalid {label}") from exc
    if not parsed.is_finite() or not signed and parsed < 0:
        raise EarnContractError(f"Invalid {label}")
    return value


def _timestamp(value: object, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    text = _plain_text(value, "timestamp", 20)
    if _UTC_RE.fullmatch(text) is None:
        raise EarnContractError("Invalid timestamp")
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise EarnContractError("Invalid timestamp") from exc
    if parsed.tzinfo != UTC:
        raise EarnContractError("Invalid timestamp")
    return text


def _exact(value: Mapping[str, object], fields: set[str], label: str) -> None:
    if set(value) != fields:
        raise EarnContractError(f"Invalid {label} fields")


@dataclass(frozen=True, slots=True)
class YieldMetric:
    kind: MetricKind
    value_percent: str | None
    period: str | None
    availability: AvailabilityState

    def __post_init__(self) -> None:
        if self.value_percent is not None:
            decimal_string(self.value_percent, "yield percentage", signed=True)
        if self.kind in {MetricKind.SUPPLY_APY, MetricKind.PROTOCOL_APR} and self.period is not None:
            raise EarnContractError("Current annual yield cannot have a historical period")
        if self.kind is MetricKind.TRAILING_RETURN:
            _plain_text(self.period, "trailing return period", 32)
        if self.availability is AvailabilityState.AVAILABLE and self.value_percent is None:
            raise EarnContractError("Available metric requires a value")
        if self.availability is AvailabilityState.UNAVAILABLE and self.value_percent is not None:
            raise EarnContractError("Unavailable metric cannot have a value")

    def to_dict(self) -> dict[str, object]:
        return {
            "availability": self.availability.value,
            "kind": self.kind.value,
            "period": self.period,
            "value_percent": self.value_percent,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> YieldMetric:
        _exact(value, {"availability", "kind", "period", "value_percent"}, "metric")
        try:
            return cls(
                MetricKind(value["kind"]),
                value["value_percent"],
                value["period"],
                AvailabilityState(value["availability"]),
            )
        except (TypeError, ValueError) as exc:
            raise EarnContractError("Invalid metric") from exc


@dataclass(frozen=True, slots=True)
class YieldPosition:
    asset_id: str
    amount: str | None
    value_usd: str | None
    availability: AvailabilityState

    def __post_init__(self) -> None:
        _identifier(self.asset_id, "position asset id")
        if self.amount is not None:
            decimal_string(self.amount, "position amount")
        if self.value_usd is not None:
            decimal_string(self.value_usd, "position USD value")
        if self.availability is AvailabilityState.AVAILABLE and (
            self.amount is None and self.value_usd is None
        ):
            raise EarnContractError("Available position requires a known value")
        if self.availability is AvailabilityState.UNAVAILABLE and (
            self.amount is not None or self.value_usd is not None
        ):
            raise EarnContractError("Unavailable position cannot contain a value")
        if self.availability is AvailabilityState.PARTIAL:
            raise EarnContractError("A position is either known or unavailable")

    def to_dict(self) -> dict[str, object]:
        return {
            "amount": self.amount,
            "asset_id": self.asset_id,
            "availability": self.availability.value,
            "value_usd": self.value_usd,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> YieldPosition:
        _exact(value, {"amount", "asset_id", "availability", "value_usd"}, "position")
        try:
            return cls(
                value["asset_id"], value["amount"], value["value_usd"],
                AvailabilityState(value["availability"]),
            )
        except (TypeError, ValueError) as exc:
            raise EarnContractError("Invalid position") from exc


@dataclass(frozen=True, slots=True)
class ExitConstraints:
    availability: AvailabilityState
    immediate: bool | None
    notice_period: str | None = None
    lockup_period: str | None = None
    fees: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.availability is AvailabilityState.PARTIAL:
            raise EarnContractError("Exit constraints cannot be partially asserted")
        if self.immediate is not None and type(self.immediate) is not bool:
            raise EarnContractError("Invalid immediate exit state")
        for value, label in (
            (self.notice_period, "notice period"), (self.lockup_period, "lockup period"),
        ):
            if value is not None:
                _plain_text(value, label, 64)
        for item in (*self.fees, *self.limitations):
            _plain_text(item, "exit constraint", 256)
        if len(self.fees) > 16 or len(self.limitations) > 16:
            raise EarnContractError("Too many exit constraints")
        if self.availability is AvailabilityState.UNAVAILABLE and any((
            self.immediate is not None, self.notice_period is not None,
            self.lockup_period is not None, self.fees, self.limitations,
        )):
            raise EarnContractError("Unavailable exit constraints cannot assert conditions")

    def to_dict(self) -> dict[str, object]:
        return {
            "availability": self.availability.value,
            "fees": list(self.fees),
            "immediate": self.immediate,
            "limitations": list(self.limitations),
            "lockup_period": self.lockup_period,
            "notice_period": self.notice_period,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> ExitConstraints:
        _exact(
            value,
            {"availability", "fees", "immediate", "limitations", "lockup_period", "notice_period"},
            "exit constraints",
        )
        if not isinstance(value["fees"], list) or not isinstance(value["limitations"], list):
            raise EarnContractError("Invalid exit constraints")
        try:
            return cls(
                AvailabilityState(value["availability"]), value["immediate"],
                value["notice_period"], value["lockup_period"],
                tuple(value["fees"]), tuple(value["limitations"]),
            )
        except (TypeError, ValueError) as exc:
            raise EarnContractError("Invalid exit constraints") from exc


@dataclass(frozen=True, slots=True)
class RiskAssessment:
    state: str = "NOT_ASSESSED"
    band: str | None = None
    factors: tuple[str, ...] = ()
    limitation: str = RISK_LIMITATION

    def __post_init__(self) -> None:
        if (
            self.state != "NOT_ASSESSED"
            or self.band is not None
            or self.factors
            or self.limitation != RISK_LIMITATION
        ):
            raise EarnContractError("M7 risk must remain NOT_ASSESSED")

    def to_dict(self) -> dict[str, object]:
        return {
            "band": self.band,
            "factors": list(self.factors),
            "limitation": self.limitation,
            "state": self.state,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> RiskAssessment:
        _exact(value, {"band", "factors", "limitation", "state"}, "risk")
        if not isinstance(value["factors"], list):
            raise EarnContractError("Invalid risk factors")
        return cls(value["state"], value["band"], tuple(value["factors"]), value["limitation"])


@dataclass(frozen=True, slots=True)
class YieldProduct:
    product_id: str
    provider_id: str
    category: ProductCategory
    protocol_id: str
    display_name: str
    network_id: str
    assets: tuple[str, ...]
    position: YieldPosition
    metrics: tuple[YieldMetric, ...]
    freshness: FreshnessState
    availability: AvailabilityState
    observed_at: str | None
    exit_constraints: ExitConstraints
    risk: RiskAssessment

    def __post_init__(self) -> None:
        _identifier(self.product_id, "product id")
        _identifier(self.provider_id, "provider id")
        if not self.product_id.startswith(f"{self.provider_id}:"):
            raise EarnContractError("Product id must be namespaced by provider")
        _identifier(self.protocol_id, "protocol id")
        _plain_text(self.display_name, "product display name", 96)
        _network(self.network_id)
        if not self.assets or len(self.assets) > 16 or len(set(self.assets)) != len(self.assets):
            raise EarnContractError("Invalid product assets")
        for asset in self.assets:
            _identifier(asset, "product asset id")
        if self.position.asset_id not in self.assets:
            raise EarnContractError("Position asset is not declared by product")
        if len(self.metrics) > 16 or len({item.kind for item in self.metrics}) != len(self.metrics):
            raise EarnContractError("Invalid product metrics")
        _timestamp(self.observed_at, optional=True)

    def to_dict(self) -> dict[str, object]:
        return {
            "assets": list(self.assets),
            "availability": self.availability.value,
            "category": self.category.value,
            "display_name": self.display_name,
            "exit_constraints": self.exit_constraints.to_dict(),
            "freshness": self.freshness.value,
            "metrics": [item.to_dict() for item in self.metrics],
            "network_id": self.network_id,
            "observed_at": self.observed_at,
            "position": self.position.to_dict(),
            "product_id": self.product_id,
            "protocol_id": self.protocol_id,
            "provider_id": self.provider_id,
            "risk": self.risk.to_dict(),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> YieldProduct:
        fields = {
            "assets", "availability", "category", "display_name", "exit_constraints",
            "freshness", "metrics", "network_id", "observed_at", "position",
            "product_id", "protocol_id", "provider_id", "risk",
        }
        _exact(value, fields, "product")
        if (
            not isinstance(value["assets"], list)
            or not isinstance(value["metrics"], list)
            or not isinstance(value["position"], Mapping)
            or not isinstance(value["exit_constraints"], Mapping)
            or not isinstance(value["risk"], Mapping)
        ):
            raise EarnContractError("Invalid product")
        try:
            return cls(
                value["product_id"], value["provider_id"], ProductCategory(value["category"]),
                value["protocol_id"], value["display_name"], value["network_id"],
                tuple(value["assets"]), YieldPosition.from_dict(value["position"]),
                tuple(YieldMetric.from_dict(item) for item in value["metrics"]),
                FreshnessState(value["freshness"]), AvailabilityState(value["availability"]),
                value["observed_at"], ExitConstraints.from_dict(value["exit_constraints"]),
                RiskAssessment.from_dict(value["risk"]),
            )
        except (TypeError, ValueError) as exc:
            raise EarnContractError("Invalid product") from exc


@dataclass(frozen=True, slots=True)
class EarnProviderResult:
    provider_id: str
    category: ProductCategory
    network_ids: tuple[str, ...]
    state: ProviderState
    source: ProviderSource
    products: tuple[YieldProduct, ...]
    observed_at: str | None
    code: str
    message: str

    def __post_init__(self) -> None:
        _identifier(self.provider_id, "provider id")
        if not self.network_ids or len(self.network_ids) > 16 or len(set(self.network_ids)) != len(self.network_ids):
            raise EarnContractError("Invalid provider networks")
        for network_id in self.network_ids:
            _network(network_id)
        if len(self.products) > MAX_PRODUCTS:
            raise EarnContractError("Too many Earn products")
        if len({item.product_id for item in self.products}) != len(self.products):
            raise EarnContractError("Duplicate Earn product")
        if any(
            item.provider_id != self.provider_id
            or item.category is not self.category
            or item.network_id not in self.network_ids
            for item in self.products
        ):
            raise EarnContractError("Provider product identity mismatch")
        if self.state is ProviderState.READY and self.source is not ProviderSource.LIVE:
            raise EarnContractError("Ready provider result must be live")
        if self.source is ProviderSource.CACHED and (
            self.state is not ProviderState.DEGRADED
            or not any(
                item.position.availability is AvailabilityState.AVAILABLE
                for item in self.products
            )
        ):
            raise EarnContractError("Invalid cached provider result")
        if self.source is ProviderSource.UNAVAILABLE and (
            self.state is not ProviderState.DEGRADED or self.products
        ):
            raise EarnContractError("Invalid unavailable provider result")
        _timestamp(self.observed_at, optional=True)
        _identifier(self.code.casefold().replace("_", "."), "provider code")
        _plain_text(self.message, "provider message", 256)

    def to_dict(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "code": self.code,
            "message": self.message,
            "network_ids": list(self.network_ids),
            "observed_at": self.observed_at,
            "products": [item.to_dict() for item in self.products],
            "provider_id": self.provider_id,
            "source": self.source.value,
            "state": self.state.value,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EarnProviderResult:
        _exact(
            value,
            {"category", "code", "message", "network_ids", "observed_at", "products", "provider_id", "source", "state"},
            "provider result",
        )
        if not isinstance(value["network_ids"], list) or not isinstance(value["products"], list):
            raise EarnContractError("Invalid provider result")
        try:
            return cls(
                value["provider_id"], ProductCategory(value["category"]),
                tuple(value["network_ids"]), ProviderState(value["state"]),
                ProviderSource(value["source"]),
                tuple(YieldProduct.from_dict(item) for item in value["products"]),
                value["observed_at"], value["code"], value["message"],
            )
        except (TypeError, ValueError) as exc:
            raise EarnContractError("Invalid provider result") from exc


def validate_account(account: object, *, optional: bool = True) -> dict[str, str] | None:
    if account is None and optional:
        return None
    if not isinstance(account, Mapping) or set(account) != {"address", "label"}:
        raise EarnContractError("Invalid Earn account")
    address = account.get("address")
    label = account.get("label")
    if not isinstance(address, str) or _ADDRESS_RE.fullmatch(address) is None:
        raise EarnContractError("Invalid Earn account address")
    return {"address": address, "label": _plain_text(label, "account label", 64)}


@dataclass(frozen=True, slots=True)
class EarnPortfolioSnapshot:
    status: PortfolioState
    account: Mapping[str, str] | None
    providers: tuple[EarnProviderResult, ...]
    total_complete: bool
    code: str
    message: str
    earn_schema_version: str = EARN_SCHEMA_VERSION
    authority_available: bool = False

    def __post_init__(self) -> None:
        if self.earn_schema_version != EARN_SCHEMA_VERSION or self.authority_available is not False:
            raise EarnContractError("Invalid Earn schema or authority state")
        validate_account(self.account)
        if len(self.providers) > 32 or len({item.provider_id for item in self.providers}) != len(self.providers):
            raise EarnContractError("Invalid Earn providers")
        if type(self.total_complete) is not bool:
            raise EarnContractError("Invalid Earn completeness")
        product_ids = [item.product_id for provider in self.providers for item in provider.products]
        if len(product_ids) > MAX_PRODUCTS or len(set(product_ids)) != len(product_ids):
            raise EarnContractError("Duplicate Earn product across providers")
        expected_complete = bool(self.providers) and all(
            provider.products
            and all(
                product.position.availability is AvailabilityState.AVAILABLE
                for product in provider.products
            )
            for provider in self.providers
        )
        any_known = any(
            product.position.availability is AvailabilityState.AVAILABLE
            for provider in self.providers for product in provider.products
        )
        expected_status = (
            PortfolioState.READY
            if expected_complete and all(
                provider.state is ProviderState.READY for provider in self.providers
            )
            else PortfolioState.PARTIAL if any_known else PortfolioState.DEGRADED
        )
        if self.total_complete is not expected_complete or self.status is not expected_status:
            raise EarnContractError("Earn portfolio state is inconsistent")
        _identifier(self.code.casefold().replace("_", "."), "portfolio code")
        _plain_text(self.message, "portfolio message", 256)

    @property
    def products(self) -> tuple[YieldProduct, ...]:
        return tuple(item for provider in self.providers for item in provider.products)

    def complete_for(self, network_id: str) -> bool:
        if network_id == "all":
            return self.total_complete
        _network(network_id)
        relevant = tuple(
            provider for provider in self.providers if network_id in provider.network_ids
        )
        return all(
            provider.products
            and all(
                product.position.availability is AvailabilityState.AVAILABLE
                for product in provider.products if network_id == "all" or product.network_id == network_id
            )
            and any(network_id == "all" or product.network_id == network_id for product in provider.products)
            for provider in relevant
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "account": dict(self.account) if self.account is not None else None,
            "authority_available": self.authority_available,
            "code": self.code,
            "earn_schema_version": self.earn_schema_version,
            "message": self.message,
            "providers": [item.to_dict() for item in self.providers],
            "status": self.status.value,
            "total_complete": self.total_complete,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> EarnPortfolioSnapshot:
        _exact(
            value,
            {"account", "authority_available", "code", "earn_schema_version", "message", "providers", "status", "total_complete"},
            "Earn portfolio",
        )
        if not isinstance(value["providers"], list):
            raise EarnContractError("Invalid Earn providers")
        account = validate_account(value["account"])
        try:
            return cls(
                PortfolioState(value["status"]), account,
                tuple(EarnProviderResult.from_dict(item) for item in value["providers"]),
                value["total_complete"], value["code"], value["message"],
                value["earn_schema_version"], value["authority_available"],
            )
        except (TypeError, ValueError) as exc:
            raise EarnContractError("Invalid Earn portfolio") from exc
