"""Adapter from the inherited Lending portfolio into normalized Earn products."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal

from .contracts import (
    AvailabilityState,
    EarnProviderResult,
    ExitConstraints,
    FreshnessState,
    MetricKind,
    ProductCategory,
    ProviderSource,
    ProviderState,
    RiskAssessment,
    YieldMetric,
    YieldPosition,
    YieldProduct,
)

LENDING_PROVIDER_ID = "holon.lending"


class LendingEarnProvider:
    provider_id = LENDING_PROVIDER_ID
    category = ProductCategory.LENDING
    network_ids = ("base",)

    def __init__(self, lending_service: object) -> None:
        self._lending_service = lending_service

    def read(
        self, account: Mapping[str, str] | None, context: Mapping[str, object],
        *, force_refresh: bool = False,
    ) -> EarnProviderResult:
        payload = context.get("lending_payload")
        if not isinstance(payload, Mapping):
            operations = context.get("operations")
            if operations is not None and not isinstance(operations, Sequence):
                operations = None
            payload = self._lending_service.read(
                account, operations, force_refresh=force_refresh,
                history_period=str(context.get("history_period", "none")),
            )
        products = tuple(
            self._product(item)
            for item in payload.get("protocols", ())
            if isinstance(item, Mapping)
        )
        observed = [item.observed_at for item in products if item.observed_at is not None]
        source = (
            ProviderSource.LIVE
            if any(item.freshness in {FreshnessState.LIVE, FreshnessState.STALE} for item in products)
            else ProviderSource.CACHED
            if any(item.freshness is FreshnessState.CACHED for item in products)
            else ProviderSource.UNAVAILABLE
        )
        return EarnProviderResult(
            self.provider_id, self.category, self.network_ids,
            ProviderState.READY if payload.get("status") == "READY" else ProviderState.DEGRADED,
            source, products, min(observed) if observed else None,
            str(payload.get("code", "LENDING_PORTFOLIO_UNAVAILABLE")),
            str(payload.get("message", "Lending portfolio is unavailable.")),
        )

    def _product(self, value: Mapping[str, object]) -> YieldProduct:
        protocol = str(value.get("protocol", "unknown"))
        market = str(value.get("market_id", "unknown"))
        atomic = value.get("position_atomic")
        amount = _units(atomic, 6) if isinstance(atomic, str) and atomic.isdecimal() else None
        position = YieldPosition(
            "usdc", amount, None,
            AvailabilityState.AVAILABLE if amount is not None else AvailabilityState.UNAVAILABLE,
        )
        base_yield = value.get("base_yield")
        apy = (
            base_yield.get("comparison_apy_percent")
            if isinstance(base_yield, Mapping) else None
        )
        metrics = (
            YieldMetric(
                MetricKind.SUPPLY_APY, str(apy), None, AvailabilityState.AVAILABLE,
            ),
        ) if isinstance(apy, str) else (
            YieldMetric(MetricKind.SUPPLY_APY, None, None, AvailabilityState.UNAVAILABLE),
        )
        freshness = {
            "LIVE": FreshnessState.LIVE,
            "STALE": FreshnessState.STALE,
            "CACHED": FreshnessState.CACHED,
        }.get(str(value.get("data_state")), FreshnessState.UNAVAILABLE)
        availability = (
            AvailabilityState.AVAILABLE
            if amount is not None and isinstance(apy, str)
            else AvailabilityState.PARTIAL
            if amount is not None or isinstance(apy, str)
            else AvailabilityState.UNAVAILABLE
        )
        return YieldProduct(
            f"{self.provider_id}:{protocol}:{market}", self.provider_id,
            self.category, protocol, str(value.get("display_name", protocol)),
            "base", ("usdc",), position, metrics, freshness, availability,
            value.get("observed_at") if isinstance(value.get("observed_at"), str) else None,
            ExitConstraints(
                AvailabilityState.AVAILABLE, None,
                limitations=(
                    "Withdrawal depends on current protocol liquidity and on-chain rules.",
                ),
            ),
            RiskAssessment(),
        )


def _units(value: str, decimals: int) -> str:
    rendered = format(Decimal(value).scaleb(-decimals), "f")
    return rendered.rstrip("0").rstrip(".") if "." in rendered else rendered
