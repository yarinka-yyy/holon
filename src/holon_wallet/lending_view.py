"""Wallet-facing presentation mapping for the public Lending portfolio."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP
from typing import Any, Mapping

from .prices import PriceSnapshot, format_usd


def lending_portfolio_to_map(
    payload: Mapping[str, Any], prices: PriceSnapshot,
) -> dict[str, object]:
    summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
    protocols = payload.get("protocols") if isinstance(payload.get("protocols"), list) else []
    mapped_protocols = [_protocol_to_map(item, prices) for item in protocols]
    visible_protocols = sorted(
        [item for item in mapped_protocols if not item["confirmedEmpty"]],
        key=lambda item: (not item["hasPosition"], mapped_protocols.index(item)),
    )
    empty_protocols = [item for item in mapped_protocols if item["confirmedEmpty"]]
    return {
        "status": str(payload.get("status", "DEGRADED")),
        "code": str(payload.get("code", "LENDING_PORTFOLIO_UNAVAILABLE")),
        "totalPosition": _usdc(summary.get("total_position_atomic")),
        "totalUsd": _usd(summary.get("total_position_atomic"), prices),
        "trackedEarnings": _earnings(summary),
        "earningsAvailable": summary.get("earnings_status") == "AVAILABLE",
        "weightedYield": _percent(summary.get("weighted_confirmed_annual_percent")),
        "yieldCompleteness": str(summary.get("yield_completeness", "PARTIAL")),
        "protocols": mapped_protocols,
        "visibleProtocols": visible_protocols,
        "emptyProtocols": empty_protocols,
        "hiddenProtocolCount": len(empty_protocols),
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
    position_atomic = item.get("position_atomic")
    position_known = isinstance(position_atomic, str) and position_atomic.isdecimal()
    has_position = position_known and int(position_atomic) > 0
    data_state = str(item.get("data_state", "UNAVAILABLE"))
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
        }.get(protocol, "assets/usdc.webp"),
        "position": _usdc(position_atomic),
        "positionUsd": _usd(position_atomic, prices),
        "positionKnown": position_known,
        "hasPosition": has_position,
        "confirmedEmpty": position_known and int(position_atomic) == 0 and data_state == "LIVE",
        "baseYield": _yield_with_metric(base),
        "comparisonYield": _percent(base.get("comparison_apy_percent")),
        "incentives": _incentives(incentive_value),
        "confirmedTotal": _percent(item.get("confirmed_total_annual_percent")),
        "completeTotal": item.get("total_completeness") == "BASE_AND_INCENTIVES",
        "earnings": _earnings(item),
        "earningsAvailable": item.get("earnings_status") == "AVAILABLE",
        "trackedSince": item.get("tracked_since") or "",
        "dataState": data_state,
        "observedAt": item.get("observed_at") or "",
        "caveats": list(item.get("caveats", [])) if isinstance(item.get("caveats"), list) else [],
    }


def _history_to_map(value: object) -> dict[str, object]:
    history = value if isinstance(value, Mapping) else {}
    points = history.get("points") if isinstance(history.get("points"), list) else []
    granularity = str(history.get("granularity", "none"))
    mapped = []
    for raw in points:
        if not isinstance(raw, Mapping):
            continue
        rates = raw.get("rates") if isinstance(raw.get("rates"), Mapping) else {}
        mapped.append({
            "observedAt": str(raw.get("observed_at", "")),
            "label": _history_label(str(raw.get("observed_at", "")), granularity),
            "position": _number(raw.get("total_position_atomic"), scale=6),
            "earnings": _number(raw.get("tracked_earnings_atomic"), scale=6),
            "aave": _number(rates.get("aave-v3")),
            "compound": _number(rates.get("compound-v3")),
            "morpho": _number(rates.get("morpho-v1")),
        })
    return {
        "period": str(history.get("period", "none")),
        "granularity": granularity,
        "periodStart": history.get("period_start") or "",
        "periodEnd": history.get("period_end") or "",
        "points": mapped,
    }


def _history_label(value: str, granularity: str) -> str:
    try:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return ""
    return (
        observed.strftime("%b %Y")
        if granularity == "month" else observed.strftime("%d %b")
    )


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
    rendered = _decimal(value, ROUND_HALF_UP)
    return f"{rendered}%" if rendered is not None else "Unavailable"


def _yield_with_metric(value: Mapping[str, Any]) -> str:
    percent = _percent(value.get("value_percent"))
    metric = value.get("metric")
    return f"{percent} {metric}" if percent != "Unavailable" and metric else "Unavailable"


def _incentives(value: object) -> str:
    percent = _percent(value)
    return f"{percent} APR" if percent != "Unavailable" else "Unknown"


def _earnings(value: Mapping[str, Any]) -> str:
    if value.get("earnings_status") != "AVAILABLE":
        return "Not enough history"
    return _usdc(value.get("tracked_earnings_atomic"))


def _usdc(value: object) -> str:
    if not isinstance(value, str) or not value.lstrip("-").isdecimal():
        return "Data unavailable"
    amount = Decimal(value).scaleb(-6)
    rendered = _decimal(amount, ROUND_DOWN)
    return f"{rendered} USDC" if rendered is not None else "Data unavailable"


def _decimal(value: object, rounding: str) -> str | None:
    if not isinstance(value, (str, Decimal)):
        return None
    try:
        number = Decimal(value)
        if not number.is_finite():
            return None
        rounded = number.quantize(Decimal("0.01"), rounding=rounding)
    except (InvalidOperation, ValueError):
        return None
    if rounded == 0:
        rounded = abs(rounded)
    sign = "−" if rounded < 0 else ""
    return f"{sign}{abs(rounded):.2f}"


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
