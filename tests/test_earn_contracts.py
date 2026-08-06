from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from holon_earn import (
    AvailabilityState,
    EarnContractError,
    EarnPortfolioService,
    EarnPortfolioSnapshot,
    EarnProviderRegistry,
    EarnProviderResult,
    EarnSnapshotStore,
    ExitConstraints,
    FreshnessState,
    LendingEarnProvider,
    MetricKind,
    ProductCategory,
    ProviderSource,
    ProviderState,
    RiskAssessment,
    YieldMetric,
    YieldPosition,
    YieldProduct,
    register_module_providers,
)
from holon_lending import LendingPortfolioService

ACCOUNT = {
    "label": "Main Account",
    "address": "0x1111111111111111111111111111111111111111",
}
OBSERVED = "2026-08-06T12:00:00Z"


def product(
    provider_id: str,
    category: ProductCategory,
    network_id: str,
    amount: str = "10",
) -> YieldProduct:
    metric = (
        YieldMetric(
            MetricKind.SUPPLY_APY, "4.25", None, AvailabilityState.AVAILABLE,
        )
        if category is ProductCategory.LENDING
        else YieldMetric(
            MetricKind.TRAILING_RETURN, "8.5", "30d", AvailabilityState.AVAILABLE,
        )
    )
    return YieldProduct(
        f"{provider_id}:primary", provider_id, category,
        "example", "Example", network_id, ("usdc",),
        YieldPosition("usdc", amount, amount, AvailabilityState.AVAILABLE),
        (metric,), FreshnessState.LIVE, AvailabilityState.AVAILABLE, OBSERVED,
        ExitConstraints(
            AvailabilityState.AVAILABLE, None,
            limitations=("Exit depends on available liquidity.",),
        ),
        RiskAssessment(),
    )


class FixtureProvider:
    def __init__(
        self, provider_id: str, category: ProductCategory, network_id: str,
    ) -> None:
        self.provider_id = provider_id
        self.category = category
        self.network_ids = (network_id,)
        self.fail = False
        self.calls = 0

    def read(self, account, context, *, force_refresh=False):
        del account, context, force_refresh
        self.calls += 1
        if self.fail:
            raise RuntimeError("fixture failure")
        return EarnProviderResult(
            self.provider_id, self.category, self.network_ids,
            ProviderState.READY, ProviderSource.LIVE,
            (product(self.provider_id, self.category, self.network_ids[0]),),
            OBSERVED, "EARN_PROVIDER_READY", "Provider is available.",
        )


def test_metric_periods_decimal_strings_and_risk_are_strict() -> None:
    with pytest.raises(EarnContractError):
        YieldMetric(
            MetricKind.SUPPLY_APY, "4", "30d", AvailabilityState.AVAILABLE,
        )
    with pytest.raises(EarnContractError):
        YieldMetric(
            MetricKind.TRAILING_RETURN, "4", None, AvailabilityState.AVAILABLE,
        )
    with pytest.raises(EarnContractError):
        YieldMetric(  # type: ignore[arg-type]
            MetricKind.SUPPLY_APY, 4.25, None, AvailabilityState.AVAILABLE,
        )
    with pytest.raises(EarnContractError):
        RiskAssessment(band="LOW")

    value = product("fixture.lending", ProductCategory.LENDING, "base")
    restored = YieldProduct.from_dict(value.to_dict())
    assert restored == value
    assert restored.risk.state == "NOT_ASSESSED"
    assert restored.risk.band is None
    assert restored.risk.factors == ()


def test_provider_failure_uses_cache_without_erasing_other_results(tmp_path: Path) -> None:
    lending = FixtureProvider("fixture.lending", ProductCategory.LENDING, "base")
    vault = FixtureProvider("fixture.vault", ProductCategory.VAULT, "hyperliquid")
    registry = EarnProviderRegistry()
    registry.register(lending)
    registry.register(vault)
    store = EarnSnapshotStore(tmp_path / "earn-snapshots.json")
    service = EarnPortfolioService(registry, store)

    live = service.read(ACCOUNT)
    assert live.total_complete
    assert live.status.value == "READY"
    vault.fail = True
    cached = service.read(ACCOUNT)

    assert cached.total_complete
    assert cached.status.value == "PARTIAL"
    assert cached.complete_for("base")
    assert cached.complete_for("all")
    assert [item.provider_id for item in cached.providers] == [
        "fixture.lending", "fixture.vault",
    ]
    cached_vault = cached.providers[1]
    assert cached_vault.source is ProviderSource.CACHED
    assert cached_vault.products[0].position.amount == "10"
    assert cached_vault.products[0].freshness is FreshnessState.CACHED
    assert not _contains_float(cached.to_dict())

    cache = json.loads((tmp_path / "earn-snapshots.json").read_text(encoding="utf-8"))
    assert set(cache) == {"accounts", "schema_version"}
    assert not _contains_float(cache)


def test_partial_refresh_keeps_each_last_confirmed_product(tmp_path: Path) -> None:
    class ChangingProvider:
        provider_id = "fixture.vault"
        category = ProductCategory.VAULT
        network_ids = ("hyperliquid",)

        def __init__(self) -> None:
            self.reads = 0

        def read(self, account, context, *, force_refresh=False):
            del account, context, force_refresh
            self.reads += 1
            primary = product(
                self.provider_id, self.category, self.network_ids[0],
                "11" if self.reads > 1 else "10",
            )
            primary = replace(primary, product_id=f"{self.provider_id}:primary")
            products = (primary,)
            if self.reads == 1:
                secondary = replace(
                    product(
                        self.provider_id, self.category, self.network_ids[0], "20",
                    ),
                    product_id=f"{self.provider_id}:secondary",
                )
                products = (primary, secondary)
            if self.reads > 2:
                raise RuntimeError("provider unavailable")
            return EarnProviderResult(
                self.provider_id, self.category, self.network_ids,
                ProviderState.READY if self.reads == 1 else ProviderState.DEGRADED,
                ProviderSource.LIVE, products, OBSERVED,
                "EARN_PROVIDER_READY" if self.reads == 1 else "EARN_PROVIDER_PARTIAL",
                "Provider is available." if self.reads == 1 else "Provider is partial.",
            )

    provider = ChangingProvider()
    registry = EarnProviderRegistry()
    registry.register(provider)
    service = EarnPortfolioService(
        registry, EarnSnapshotStore(tmp_path / "earn-snapshots.json"),
    )

    first = service.read(ACCOUNT)
    partial = service.read(ACCOUNT)
    failed = service.read(ACCOUNT)

    assert [item.position.amount for item in first.products] == ["10", "20"]
    assert [item.position.amount for item in partial.products] == ["11", "20"]
    assert partial.providers[0].source is ProviderSource.CACHED
    assert partial.products[1].freshness is FreshnessState.CACHED
    assert [item.position.amount for item in failed.products] == ["11", "20"]
    assert all(item.freshness is FreshnessState.CACHED for item in failed.products)


def test_ready_refresh_removes_product_from_last_confirmed_cache(tmp_path: Path) -> None:
    class AuthoritativeProvider:
        provider_id = "fixture.vault"
        category = ProductCategory.VAULT
        network_ids = ("hyperliquid",)

        def __init__(self) -> None:
            self.reads = 0

        def read(self, account, context, *, force_refresh=False):
            del account, context, force_refresh
            self.reads += 1
            if self.reads > 2:
                raise RuntimeError("provider unavailable")
            primary = replace(
                product(self.provider_id, self.category, self.network_ids[0], "10"),
                product_id=f"{self.provider_id}:primary",
            )
            products = (primary,)
            if self.reads == 1:
                secondary = replace(
                    product(self.provider_id, self.category, self.network_ids[0], "20"),
                    product_id=f"{self.provider_id}:secondary",
                )
                products = (primary, secondary)
            return EarnProviderResult(
                self.provider_id, self.category, self.network_ids,
                ProviderState.READY, ProviderSource.LIVE, products,
                OBSERVED, "EARN_PROVIDER_READY", "Provider is available.",
            )

    provider = AuthoritativeProvider()
    registry = EarnProviderRegistry()
    registry.register(provider)
    service = EarnPortfolioService(
        registry, EarnSnapshotStore(tmp_path / "earn-snapshots.json"),
    )

    assert len(service.read(ACCOUNT).products) == 2
    assert [item.product_id for item in service.read(ACCOUNT).products] == [
        "fixture.vault:primary",
    ]
    assert [item.product_id for item in service.read(ACCOUNT).products] == [
        "fixture.vault:primary",
    ]


def test_cache_with_changed_provider_network_is_ignored(tmp_path: Path) -> None:
    path = tmp_path / "earn-snapshots.json"
    original = FixtureProvider("fixture.vault", ProductCategory.VAULT, "base")
    first_registry = EarnProviderRegistry()
    first_registry.register(original)
    EarnPortfolioService(first_registry, EarnSnapshotStore(path)).read(ACCOUNT)

    changed = FixtureProvider(
        "fixture.vault", ProductCategory.VAULT, "hyperliquid",
    )
    changed.fail = True
    changed_registry = EarnProviderRegistry()
    changed_registry.register(changed)
    result = EarnPortfolioService(
        changed_registry, EarnSnapshotStore(path),
    ).read(ACCOUNT)

    assert result.providers[0].source is ProviderSource.UNAVAILABLE
    assert result.providers[0].products == ()
    assert not result.total_complete


def test_installed_uncached_provider_marks_only_relevant_totals_incomplete(
    tmp_path: Path,
) -> None:
    lending = FixtureProvider("fixture.lending", ProductCategory.LENDING, "base")
    vault = FixtureProvider("fixture.vault", ProductCategory.VAULT, "hyperliquid")
    vault.fail = True
    registry = EarnProviderRegistry()
    registry.register(lending)
    registry.register(vault)
    service = EarnPortfolioService(registry, EarnSnapshotStore(tmp_path / "earn-snapshots.json"))

    result = service.read(ACCOUNT)

    assert not result.total_complete
    assert result.complete_for("base")
    assert not result.complete_for("all")
    assert result.providers[0].products[0].position.amount == "10"
    assert result.providers[1].source is ProviderSource.UNAVAILABLE


def test_removed_provider_cache_is_ignored_and_pruned(tmp_path: Path) -> None:
    cache_path = tmp_path / "earn-snapshots.json"
    lending = FixtureProvider("fixture.lending", ProductCategory.LENDING, "base")
    vault = FixtureProvider("fixture.vault", ProductCategory.VAULT, "hyperliquid")
    both = EarnProviderRegistry()
    both.register(lending)
    both.register(vault)
    EarnPortfolioService(both, EarnSnapshotStore(cache_path)).read(ACCOUNT)

    base = EarnProviderRegistry()
    base.register(lending)
    result = EarnPortfolioService(base, EarnSnapshotStore(cache_path)).read(ACCOUNT)

    assert result.total_complete
    assert [item.provider_id for item in result.providers] == ["fixture.lending"]
    stored = json.loads(cache_path.read_text(encoding="utf-8"))
    assert [
        item["provider_id"] for item in stored["accounts"][0]["providers"]
    ] == ["fixture.lending"]


def test_unknown_fields_and_malformed_cache_fail_closed(tmp_path: Path) -> None:
    value = EarnPortfolioService.unavailable(ACCOUNT).to_dict()
    value["unexpected"] = True
    with pytest.raises(EarnContractError):
        EarnPortfolioSnapshot.from_dict(value)

    path = tmp_path / "earn-snapshots.json"
    path.write_text('{"schema_version":"1","accounts":[],"extra":true}', encoding="utf-8")
    provider = FixtureProvider("fixture.lending", ProductCategory.LENDING, "base")
    registry = EarnProviderRegistry()
    registry.register(provider)
    result = EarnPortfolioService(registry, EarnSnapshotStore(path)).read(ACCOUNT)
    assert result.providers[0].source is ProviderSource.LIVE
    assert path.read_text(encoding="utf-8").endswith('"extra":true}')


def test_provider_source_cannot_claim_cached_data_is_ready() -> None:
    ready = FixtureProvider(
        "fixture.vault", ProductCategory.VAULT, "hyperliquid",
    ).read(ACCOUNT, {})

    with pytest.raises(EarnContractError):
        replace(ready, source=ProviderSource.CACHED)


def test_cache_rejects_invalid_saved_at_without_overwriting(tmp_path: Path) -> None:
    path = tmp_path / "earn-snapshots.json"
    provider = FixtureProvider("fixture.lending", ProductCategory.LENDING, "base")
    registry = EarnProviderRegistry()
    registry.register(provider)
    service = EarnPortfolioService(registry, EarnSnapshotStore(path))
    service.read(ACCOUNT)
    value = json.loads(path.read_text(encoding="utf-8"))
    value["accounts"][0]["providers"][0]["saved_at"] = "2026-99-99T12:00:00Z"
    malformed = json.dumps(value, sort_keys=True, separators=(",", ":"))
    path.write_text(malformed, encoding="utf-8")

    result = service.read(ACCOUNT)

    assert result.providers[0].source is ProviderSource.LIVE
    assert path.read_text(encoding="utf-8") == malformed


def test_lending_adapter_preserves_three_protocol_identities_and_decimal_amounts() -> None:
    payload = LendingPortfolioService.unavailable(ACCOUNT)
    payload.update({
        "status": "READY", "code": "LENDING_PORTFOLIO_READY",
        "message": "Lending portfolio is available.",
    })
    for index, item in enumerate(payload["protocols"]):
        item.update({
            "position_atomic": str((index + 1) * 1_250_000),
            "data_state": "LIVE", "observed_at": OBSERVED,
            "base_yield": {
                "comparison_apy_percent": str(index + 3),
                "metric": "APY", "value_percent": str(index + 3),
            },
        })

    class Service:
        def read(self, account, operations, **kwargs):
            del account, operations, kwargs
            return payload

    result = LendingEarnProvider(Service()).read(ACCOUNT, {})

    assert [item.protocol_id for item in result.products] == [
        "aave-v3", "compound-v3", "morpho-v1",
    ]
    assert [item.position.amount for item in result.products] == ["1.25", "2.5", "3.75"]
    assert all(item.metrics[0].kind is MetricKind.SUPPLY_APY for item in result.products)
    assert all(item.risk.state == "NOT_ASSESSED" for item in result.products)


def test_module_provider_receives_only_declared_identity_or_degrades() -> None:
    valid = FixtureProvider("module.vault", ProductCategory.VAULT, "hyperliquid")
    invalid = FixtureProvider("wrong.id", ProductCategory.VAULT, "hyperliquid")
    capabilities = (
        SimpleNamespace(
            declaration=SimpleNamespace(
                capability_id="module.vault.wallet",
                descriptor={
                    "category": "VAULT", "network_ids": ["hyperliquid"],
                    "provider_id": "module.vault",
                },
            ),
            contribution=valid,
        ),
        SimpleNamespace(
            declaration=SimpleNamespace(
                capability_id="module.invalid.wallet",
                descriptor={
                    "category": "VAULT", "network_ids": ["hyperliquid"],
                    "provider_id": "module.invalid",
                },
            ),
            contribution=invalid,
        ),
    )
    module_registry = SimpleNamespace(capabilities=lambda kind: capabilities if kind == "earn_provider" else ())
    registry = EarnProviderRegistry()
    register_module_providers(registry, module_registry)

    result = EarnPortfolioService(registry).read(ACCOUNT)

    assert [item.provider_id for item in result.providers] == ["module.invalid", "module.vault"]
    assert result.providers[0].source is ProviderSource.UNAVAILABLE
    assert result.providers[1].source is ProviderSource.LIVE
    assert not result.total_complete


def _contains_float(value: object) -> bool:
    if isinstance(value, float):
        return True
    if isinstance(value, dict):
        return any(_contains_float(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_float(item) for item in value)
    return False
