"""Public normalized Earn contracts and aggregation services."""

from .contracts import (
    EARN_SCHEMA_VERSION,
    RISK_LIMITATION,
    AvailabilityState,
    EarnContractError,
    EarnPortfolioSnapshot,
    EarnProviderResult,
    ExitConstraints,
    FreshnessState,
    MetricKind,
    PortfolioState,
    ProductCategory,
    ProviderSource,
    ProviderState,
    RiskAssessment,
    YieldMetric,
    YieldPosition,
    YieldProduct,
    decimal_string,
)
from .lending import LENDING_PROVIDER_ID, LendingEarnProvider
from .service import (
    EarnPortfolioService,
    EarnProvider,
    EarnProviderRegistry,
    register_module_providers,
)
from .store import EarnSnapshotStore

__all__ = [
    "EARN_SCHEMA_VERSION", "RISK_LIMITATION", "AvailabilityState",
    "EarnContractError", "EarnPortfolioService", "EarnPortfolioSnapshot",
    "EarnProvider", "EarnProviderRegistry", "EarnProviderResult",
    "EarnSnapshotStore", "ExitConstraints", "FreshnessState",
    "LENDING_PROVIDER_ID", "LendingEarnProvider", "MetricKind",
    "PortfolioState", "ProductCategory", "ProviderSource", "ProviderState",
    "RiskAssessment", "YieldMetric", "YieldPosition", "YieldProduct",
    "decimal_string", "register_module_providers",
]
