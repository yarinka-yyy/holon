"""Wallet-facing presentation mapping for the public Lending portfolio."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from .prices import PriceSnapshot, format_usd


def lending_portfolio_to_map(
    payload: Mapping[str, Any], prices: PriceSnapshot,
) -> dict[str, object]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    protocols = payload.get("protocols") if isinstance(payload.get("protocols"), list) else []
    return {
        "status": str(payload.get("status", "DEGRADED")),
        "code": str(payload.get("code", "LENDING_PORTFOLIO_UNAVAILABLE")),
        "totalPosition": summary.get("display_total_position") or "Data unavailable",
        "totalUsd": _usd(summary.get("total_position_atomic"), prices),
        "trackedEarnings": summary.get("display_tracked_earnings") or "Not enough history",
        "earningsAvailable": summary.get("earnings_status") == "AVAILABLE",
        "weightedYield": _percent(summary.get("weighted_confirmed_annual_percent")),
        "yieldCompleteness": str(summary.get("yield_completeness", "PARTIAL")),
        "protocols": [_protocol_to_map(item, prices) for item in protocols],
        "recommendation": _recommendation(payload.get("recommendation")),
        "updatedText": _updated_text(payload.get("delivery")),
        "cached": _cached(payload.get("delivery")),
        "history": _history_to_map(payload.get("history")),
    }


def _protocol_to_map(value: object, prices: PriceSnapshot) -> dict[str, object]:
    item = value if isinstance(value, Mapping) else {}
    protocol = str(item.get("protocol", ""))
    base = item.get("base_yield") if isinstance(item.get("base_yield"), Mapping) else {}
    incentives = item.get("incentives") if isinstance(item.get("incentives"), Mapping) else {}
    incentive_value = incentives.get("total_apr_percent")
    return {
        "protocol": protocol,
        "name": {
            "aave-v3": "Aave V3", "compound-v3": "Compound III",
            "morpho-v1": "Morpho V1",
        }.get(protocol, str(item.get("display_name", "Lending"))),
        "logo": {
            "aave-v3": "assets/aave-logo-white.png",
            "compound-v3": "assets/compound-logo-white.svg",
            "morpho-v1": "assets/morpho-logo-white.svg",
        }.get(protocol, "assets/usdc.png"),
        "position": item.get("display_position") or "Data unavailable",
        "positionUsd": _usd(item.get("position_atomic"), prices),
        "baseYield": (
            f"{base.get('value_percent')}% {base.get('metric')}"
            if base.get("value_percent") is not None else "Unavailable"
        ),
        "comparisonYield": _percent(base.get("comparison_apy_percent")),
        "incentives": (
            f"{incentive_value}% APR" if incentive_value is not None else "Unknown"
        ),
        "confirmedTotal": _percent(item.get("confirmed_total_annual_percent")),
        "completeTotal": item.get("total_completeness") == "BASE_AND_INCENTIVES",
        "earnings": item.get("display_tracked_earnings") or "Not enough history",
        "earningsAvailable": item.get("earnings_status") == "AVAILABLE",
        "trackedSince": item.get("tracked_since") or "",
        "dataState": str(item.get("data_state", "UNAVAILABLE")),
        "observedAt": item.get("observed_at") or "",
        "caveats": list(item.get("caveats", [])) if isinstance(item.get("caveats"), list) else [],
    }


def _history_to_map(value: object) -> dict[str, object]:
    history = value if isinstance(value, Mapping) else {}
    points = history.get("points") if isinstance(history.get("points"), list) else []
    mapped = []
    for raw in points:
        if not isinstance(raw, Mapping):
            continue
        rates = raw.get("rates") if isinstance(raw.get("rates"), Mapping) else {}
        mapped.append({
            "observedAt": str(raw.get("observed_at", "")),
            "position": _number(raw.get("total_position_atomic"), scale=6),
            "earnings": _number(raw.get("tracked_earnings_atomic"), scale=6),
            "aave": _number(rates.get("aave-v3")),
            "compound": _number(rates.get("compound-v3")),
            "morpho": _number(rates.get("morpho-v1")),
        })
    return {"period": str(history.get("period", "none")), "points": mapped}


def _recommendation(value: object) -> dict[str, object]:
    item = value if isinstance(value, Mapping) else {}
    return {
        "protocol": str(item.get("protocol", "")),
        "yield": _percent(item.get("confirmed_total_annual_percent")),
        "incomplete": item.get("incomplete_comparison") is True,
    }


def _usd(atomic: object, prices: PriceSnapshot) -> str:
    if not isinstance(atomic, str) or not atomic.isdecimal():
        return "Data unavailable"
    price = prices.by_asset.get("usdc")
    if price is None or price.value is None:
        return "Data unavailable"
    return format_usd(Decimal(atomic).scaleb(-6) * price.value)


def _percent(value: object) -> str:
    return f"{value}%" if isinstance(value, str) else "Unavailable"


def _number(value: object, scale: int = 0) -> float | None:
    if not isinstance(value, str) or not value.lstrip("-").isdecimal():
        return None
    return float(Decimal(value).scaleb(-scale))


def _cached(value: object) -> bool:
    return isinstance(value, Mapping) and value.get("source") in {
        "MEMORY_CACHE", "PERSISTED_FALLBACK",
    }


def _updated_text(value: object) -> str:
    if not isinstance(value, Mapping) or value.get("fetched_at") is None:
        return "Data unavailable"
    prefix = "Cached · " if _cached(value) else "Updated · "
    return prefix + str(value["fetched_at"])[11:16] + " UTC"
