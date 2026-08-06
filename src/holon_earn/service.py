"""Provider registry and failure-isolated normalized Earn aggregation."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Protocol

from .contracts import (
    AvailabilityState,
    EarnContractError,
    EarnPortfolioSnapshot,
    EarnProviderResult,
    PortfolioState,
    ProductCategory,
    ProviderSource,
    ProviderState,
    validate_account,
)
from .store import EarnSnapshotStore


class EarnProvider(Protocol):
    provider_id: str
    category: ProductCategory
    network_ids: tuple[str, ...]

    def read(
        self,
        account: Mapping[str, str] | None,
        context: Mapping[str, object],
        *,
        force_refresh: bool = False,
    ) -> EarnProviderResult: ...


class EarnProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, EarnProvider] = {}

    def register(self, provider: EarnProvider) -> None:
        provider_id = getattr(provider, "provider_id", None)
        category = getattr(provider, "category", None)
        networks = getattr(provider, "network_ids", None)
        if (
            not isinstance(provider_id, str)
            or not isinstance(category, ProductCategory)
            or not isinstance(networks, tuple)
            or not networks
            or not callable(getattr(provider, "read", None))
        ):
            raise EarnContractError("Earn provider does not implement the bounded interface")
        if provider_id in self._providers:
            raise EarnContractError("Duplicate Earn provider")
        self._providers[provider_id] = provider

    def providers(self) -> tuple[EarnProvider, ...]:
        return tuple(self._providers[key] for key in sorted(self._providers))

    @property
    def provider_ids(self) -> frozenset[str]:
        return frozenset(self._providers)


class UnavailableEarnProvider:
    def __init__(
        self, provider_id: str, category: ProductCategory, network_ids: tuple[str, ...],
    ) -> None:
        self.provider_id = provider_id
        self.category = category
        self.network_ids = network_ids

    def read(
        self, account: Mapping[str, str] | None, context: Mapping[str, object],
        *, force_refresh: bool = False,
    ) -> EarnProviderResult:
        del account, context, force_refresh
        raise RuntimeError("Earn provider is unavailable")


def register_module_providers(registry: EarnProviderRegistry, module_registry: object) -> None:
    capabilities = getattr(module_registry, "capabilities", None)
    if not callable(capabilities):
        return
    active = {
        item.declaration.capability_id: item
        for item in capabilities("earn_provider")
    }
    declarations = getattr(module_registry, "declared_capabilities", None)
    declared = (
        declarations("earn_provider")
        if callable(declarations) else tuple(active.values())
    )
    for capability in declared:
        declaration = capability.declaration
        descriptor = declaration.descriptor
        try:
            provider_id = descriptor["provider_id"]
            category = ProductCategory(descriptor["category"])
            network_ids = tuple(descriptor["network_ids"])
            contribution = active[declaration.capability_id].contribution
            if (
                contribution.provider_id != provider_id
                or contribution.category is not category
                or contribution.network_ids != network_ids
            ):
                raise EarnContractError("Module Earn provider identity mismatch")
            registry.register(contribution)
        except Exception:
            try:
                registry.register(UnavailableEarnProvider(
                    descriptor["provider_id"],
                    ProductCategory(descriptor["category"]),
                    tuple(descriptor["network_ids"]),
                ))
            except Exception:
                continue


class EarnPortfolioService:
    def __init__(
        self, registry: EarnProviderRegistry, store: EarnSnapshotStore | None = None,
    ) -> None:
        self.registry = registry
        self.store = store

    def read(
        self,
        account: Mapping[str, str] | None,
        *,
        provider_contexts: Mapping[str, Mapping[str, object]] | None = None,
        force_refresh: bool = False,
    ) -> EarnPortfolioSnapshot:
        normalized = validate_account(account)
        contexts = provider_contexts or {}
        results = tuple(
            self._read_provider(
                provider, normalized, contexts.get(provider.provider_id, {}), force_refresh,
            )
            for provider in self.registry.providers()
        )
        return self._snapshot(normalized, results)

    def cached(self, account: Mapping[str, str] | None) -> EarnPortfolioSnapshot:
        normalized = validate_account(account)
        results = tuple(
            self._cached_or_unavailable(provider, normalized)
            for provider in self.registry.providers()
        )
        return self._snapshot(normalized, results)

    def _read_provider(
        self,
        provider: EarnProvider,
        account: Mapping[str, str] | None,
        context: Mapping[str, object],
        force_refresh: bool,
    ) -> EarnProviderResult:
        try:
            if not isinstance(context, Mapping):
                raise EarnContractError("Invalid provider context")
            if provider.provider_id != "holon.lending" and context:
                raise EarnContractError("Optional Earn providers receive no Core context")
            result = provider.read(account, context, force_refresh=force_refresh)
            if (
                not isinstance(result, EarnProviderResult)
                or result.provider_id != provider.provider_id
                or result.category is not provider.category
                or result.network_ids != provider.network_ids
            ):
                raise EarnContractError("Invalid provider result identity")
            if self.store is not None and account is not None:
                cached = self._compatible_cache(provider, account)
                merged, used_cache = _merge_cached_products(result, cached)
                try:
                    if (
                        result.source is ProviderSource.LIVE
                        and any(
                            product.position.availability is AvailabilityState.AVAILABLE
                            for product in result.products
                        )
                    ):
                        stored = (
                            EarnProviderResult(
                                merged.provider_id, merged.category, merged.network_ids,
                                merged.state, ProviderSource.LIVE, merged.products,
                                merged.observed_at, merged.code, merged.message,
                            )
                            if used_cache else result
                        )
                        self.store.save(
                            account, stored, self.registry.provider_ids, _now(),
                        )
                except OSError:
                    pass
                return merged
            return result
        except Exception:
            return self._cached_or_unavailable(provider, account)

    def _cached_or_unavailable(
        self, provider: EarnProvider, account: Mapping[str, str] | None,
    ) -> EarnProviderResult:
        if self.store is not None and account is not None:
            cached = self._compatible_cache(provider, account)
            if cached is not None:
                return cached
        return EarnProviderResult(
            provider.provider_id, provider.category, provider.network_ids,
            ProviderState.DEGRADED, ProviderSource.UNAVAILABLE, (), None,
            "EARN_PROVIDER_UNAVAILABLE", "Earn provider is unavailable.",
        )

    def _compatible_cache(
        self, provider: EarnProvider, account: Mapping[str, str],
    ) -> EarnProviderResult | None:
        assert self.store is not None
        cached = self.store.load(
            account, provider.provider_id, self.registry.provider_ids,
        )
        if (
            cached is None
            or cached.category is not provider.category
            or cached.network_ids != provider.network_ids
        ):
            return None
        return cached

    @staticmethod
    def _snapshot(
        account: Mapping[str, str] | None,
        providers: tuple[EarnProviderResult, ...],
    ) -> EarnPortfolioSnapshot:
        complete = bool(providers) and all(
            provider.products
            and all(
                product.position.availability is AvailabilityState.AVAILABLE
                for product in provider.products
            )
            for provider in providers
        )
        any_known = any(
            product.position.availability is AvailabilityState.AVAILABLE
            for provider in providers for product in provider.products
        )
        ready = complete and all(
            provider.state is ProviderState.READY for provider in providers
        )
        status = (
            PortfolioState.READY if ready
            else PortfolioState.PARTIAL if any_known
            else PortfolioState.DEGRADED
        )
        code, message = {
            PortfolioState.READY: ("EARN_PORTFOLIO_READY", "Earn portfolio is available."),
            PortfolioState.PARTIAL: ("EARN_PORTFOLIO_PARTIAL", "Some Earn provider data is cached or unavailable."),
            PortfolioState.DEGRADED: ("EARN_PORTFOLIO_UNAVAILABLE", "Earn portfolio is unavailable."),
        }[status]
        return EarnPortfolioSnapshot(status, account, providers, complete, code, message)

    @classmethod
    def unavailable(
        cls, account: Mapping[str, str] | None,
    ) -> EarnPortfolioSnapshot:
        normalized = validate_account(account)
        return EarnPortfolioSnapshot(
            PortfolioState.DEGRADED, normalized, (), False,
            "EARN_PORTFOLIO_UNAVAILABLE", "Earn portfolio is unavailable.",
        )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _merge_cached_products(
    current: EarnProviderResult,
    cached: EarnProviderResult | None,
) -> tuple[EarnProviderResult, bool]:
    if cached is None or current.state is ProviderState.READY:
        return current, False
    products = {item.product_id: item for item in current.products}
    used_cache = False
    for cached_product in cached.products:
        live_product = products.get(cached_product.product_id)
        if (
            live_product is None
            or live_product.position.availability is not AvailabilityState.AVAILABLE
        ):
            products[cached_product.product_id] = cached_product
            used_cache = True
    if not used_cache:
        return current, False
    return EarnProviderResult(
        current.provider_id, current.category, current.network_ids,
        ProviderState.DEGRADED, ProviderSource.CACHED,
        tuple(products[key] for key in sorted(products)),
        current.observed_at or cached.observed_at,
        "EARN_PROVIDER_PARTIAL",
        "Some products use the last confirmed public snapshot.",
    ), True
