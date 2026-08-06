"""Official HLP adapter for the normalized Holon Earn boundary."""

from __future__ import annotations

from collections.abc import Mapping

from holon_earn import (
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

from .profile import HLP_ADDRESS, HLP_NAME
from .reader import HyperliquidReader

PROVIDER_ID = "holon.perpdex.hyperliquid.hlp"


class HlpEarnProvider:
    provider_id = PROVIDER_ID
    category = ProductCategory.VAULT
    network_ids = ("hyperliquid-mainnet",)

    def __init__(self, reader: HyperliquidReader) -> None:
        self._reader = reader

    def read(
        self,
        account: Mapping[str, str] | None,
        context: Mapping[str, object],
        *,
        force_refresh: bool = False,
    ) -> EarnProviderResult:
        del force_refresh
        if context or account is None:
            raise RuntimeError("HLP account is unavailable")
        result = self._reader.hlp(account)
        if result.get("status") != "READY":
            raise RuntimeError("HLP data is unavailable")
        observed_at = result.get("observed_at")
        equity = result.get("equity_usdc")
        apr = result.get("protocol_apr_percent")
        if not isinstance(observed_at, str) or not isinstance(equity, str) or not isinstance(apr, str):
            raise RuntimeError("HLP data is invalid")
        unlocked = result.get("unlocked") is True
        product = YieldProduct(
            f"{self.provider_id}:official-hlp",
            self.provider_id,
            self.category,
            "hyperliquid-hlp",
            HLP_NAME,
            "hyperliquid-mainnet",
            ("usdc",),
            YieldPosition("usdc", equity, equity, AvailabilityState.AVAILABLE),
            (
                YieldMetric(
                    MetricKind.PROTOCOL_APR,
                    apr,
                    None,
                    AvailabilityState.AVAILABLE,
                ),
                YieldMetric(
                    MetricKind.TRAILING_RETURN,
                    None,
                    "30d",
                    AvailabilityState.UNAVAILABLE,
                ),
            ),
            FreshnessState.LIVE,
            AvailabilityState.AVAILABLE,
            observed_at,
            ExitConstraints(
                AvailabilityState.AVAILABLE,
                unlocked,
                lockup_period="4 days after the latest deposit",
                fees=("Official protocol HLP has no Holon fee.",),
                limitations=(
                    "A new deposit restarts the four-day withdrawal lock-up.",
                    f"Only official parent HLP {HLP_ADDRESS} is supported.",
                ),
            ),
            RiskAssessment(),
        )
        return EarnProviderResult(
            self.provider_id,
            self.category,
            self.network_ids,
            ProviderState.READY,
            ProviderSource.LIVE,
            (product,),
            observed_at,
            "HLP_EARN_READY",
            "Official HLP Earn data is available.",
        )
