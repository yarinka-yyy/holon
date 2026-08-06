"""Wallet-only presentation mapping for normalized secret-free Earn data."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP

from holon_contracts.registry import load_registry
from holon_earn import (
    AvailabilityState,
    EarnPortfolioSnapshot,
    MetricKind,
    ProductCategory,
    ProviderSource,
    YieldProduct,
)

from .prices import MarketPriceSnapshot, PriceSnapshot, market_snapshot_from_chainlink


def earn_portfolio_to_map(
    snapshot: EarnPortfolioSnapshot,
    prices: MarketPriceSnapshot | PriceSnapshot,
    selected_filter: str,
) -> dict[str, object]:
    filters = [{"id": "all", "label": "All"}, {"id": "lending", "label": "Lending"}]
    if any(provider.category is ProductCategory.VAULT for provider in snapshot.providers):
        filters.append({"id": "vaults", "label": "Vaults"})
    allowed = {item["id"] for item in filters}
    current = selected_filter if selected_filter in allowed else "all"
    products = tuple(
        product for product in snapshot.products
        if current == "all"
        or current == "lending" and product.category is ProductCategory.LENDING
        or current == "vaults" and product.category is ProductCategory.VAULT
    )
    mapped = [_product_to_map(item, prices) for item in products]
    sources = {provider.source for provider in snapshot.providers}
    updated = (
        "Live provider data"
        if sources and sources == {ProviderSource.LIVE}
        else "Cached or partial provider data"
        if ProviderSource.CACHED in sources
        else "Provider data unavailable"
    )
    return {
        "availableFilters": filters,
        "filter": current,
        "products": mapped,
        "vaultProducts": [
            item for item, product in zip(mapped, products)
            if product.category is ProductCategory.VAULT
        ],
        "showLending": current in {"all", "lending"},
        "showVaults": current in {"all", "vaults"}
        and any(provider.category is ProductCategory.VAULT for provider in snapshot.providers),
        "status": snapshot.status.value,
        "totalComplete": snapshot.total_complete,
        "updatedText": updated,
        "message": snapshot.message,
    }


def _product_to_map(
    product: YieldProduct,
    prices: MarketPriceSnapshot | PriceSnapshot,
) -> dict[str, object]:
    metric = product.metrics[0] if product.metrics else None
    metric_label = (
        "Supply APY" if metric is not None and metric.kind is MetricKind.SUPPLY_APY
        else "Protocol APR" if metric is not None and metric.kind is MetricKind.PROTOCOL_APR
        else f"Trailing return · {metric.period}"
        if metric is not None and metric.kind is MetricKind.TRAILING_RETURN
        else "Yield"
    )
    metric_value = (
        f"{_rounded(metric.value_percent, 2)}%"
        if metric is not None and metric.value_percent is not None
        else "Data unavailable"
    )
    amount = product.position.amount
    asset = _asset_symbol(product.position.asset_id)
    position = (
        f"{_rounded(amount, 2, down=True)} {asset}"
        if amount is not None else "Data unavailable"
    )
    usd = _position_usd(product, prices)
    exit_text = _exit_constraints_text(product)
    return {
        "availability": product.availability.value,
        "category": product.category.value,
        "dataState": product.freshness.value,
        "displayName": product.display_name,
        "exitConstraints": exit_text,
        "metricLabel": metric_label,
        "metricValue": metric_value,
        "networkId": product.network_id,
        "position": position,
        "positionUsd": f"${_rounded(usd, 2)}" if usd is not None else "Data unavailable",
        "productId": product.product_id,
        "protocolId": product.protocol_id,
        "providerId": product.provider_id,
        "riskBand": "",
        "riskState": "Not assessed",
    }


def _position_usd(
    product: YieldProduct,
    prices: MarketPriceSnapshot | PriceSnapshot,
) -> str | None:
    if product.position.value_usd is not None:
        return product.position.value_usd
    if product.position.amount is None:
        return None
    registry = load_registry()
    meta = registry.asset_by_id.get(product.position.asset_id)
    if meta is None or meta.market_price_id is None:
        return None
    market = (
        market_snapshot_from_chainlink(prices)
        if isinstance(prices, PriceSnapshot) else prices
    ).by_market.get(meta.market_price_id)
    if market is None or market.value_usd is None:
        return None
    return format(Decimal(product.position.amount) * market.value_usd, "f")


def _asset_symbol(asset_id: str) -> str:
    meta = load_registry().asset_by_id.get(asset_id)
    return meta.display_symbol if meta is not None else asset_id.upper()


def _exit_constraints_text(product: YieldProduct) -> str:
    constraints = product.exit_constraints
    if constraints.availability is AvailabilityState.UNAVAILABLE:
        return "Exit conditions unavailable"
    parts: list[str] = []
    if constraints.immediate is not None:
        parts.append("Immediate exit" if constraints.immediate else "No immediate exit")
    if constraints.notice_period is not None:
        parts.append(f"Notice {constraints.notice_period}")
    if constraints.lockup_period is not None:
        parts.append(f"Lockup {constraints.lockup_period}")
    parts.extend(constraints.fees)
    parts.extend(constraints.limitations)
    return " · ".join(parts) if parts else "No exit conditions supplied"


def _rounded(value: str | None, places: int, *, down: bool = False) -> str:
    if value is None:
        return ""
    try:
        parsed = Decimal(value)
        quantum = Decimal(1).scaleb(-places)
        rendered = parsed.quantize(
            quantum, rounding=ROUND_DOWN if down else ROUND_HALF_UP,
        )
    except (InvalidOperation, ValueError):
        return ""
    return f"{rendered:.{places}f}"
