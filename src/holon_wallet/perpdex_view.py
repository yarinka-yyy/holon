"""Secret-free, human-readable presentation for protected PerpDEX actions."""

from __future__ import annotations

from decimal import Decimal
from datetime import datetime
from typing import Mapping


def action_presentation(action: Mapping[str, object]) -> dict[str, object]:
    kind = str(action.get("actionType", ""))
    phases = action.get("phases") if isinstance(action.get("phases"), list) else []
    intent = action.get("intent") if isinstance(action.get("intent"), Mapping) else {}
    order = _phase(phases, "PLACE_IOC_ORDER")
    funding = action.get("funding") if isinstance(action.get("funding"), Mapping) else {}
    market = str(order.get("market") or intent.get("market") or "")
    if kind == "FUND_TRADING_ACCOUNT":
        amount = _usdc_atomic(str(funding.get("amountAtomic", "0")))
        return _base("Deposit", f"Deposit {amount} to Hyperliquid", "Arbitrum One · Native USDC", [
            ("Network", "Arbitrum One"), ("Asset", "Native USDC"),
            ("Destination", "Your Hyperliquid trading balance"),
            ("Maximum network fee", _eth(str(funding.get("maxTotalFeeWei", "0")))),
        ], ["A completed Arbitrum transfer is credited by Hyperliquid separately."], action, {
            "Network / chain": "Arbitrum One · " + str(funding.get("chainId", "")),
            "Native USDC contract": str(funding.get("tokenContract", "")),
            "Hyperliquid Bridge2": str(funding.get("recipient", "")),
            "Maximum fee (wei)": str(funding.get("maxTotalFeeWei", "")),
        })
    side = "long" if order.get("is_buy") is True else "short"
    mode = "Cross" if _phase(phases, "SET_ISOLATED_LEVERAGE").get("is_cross") is True else "Isolated"
    leverage = _phase(phases, "SET_ISOLATED_LEVERAGE").get("leverage") or intent.get("leverage")
    if kind == "OPEN_POSITION":
        notional = _number(str(intent.get("notional_usdc", "0")))
        margin = _divided(str(intent.get("notional_usdc", "0")), leverage)
        rows = [
            ("Margin", f"≈ {margin} USDC"), ("Maximum position", f"≤ {notional} USDC"),
            ("Size", f"{_number(str(order.get('size_asset', '0')))} {market}"),
            ("Reference price", f"≈ {_number(str(order.get('reference_price', '0')))} USDC"),
            ("IOC price limit", f"{_number(str(order.get('limit_price', '0')))} USDC"),
            ("Price protection", f"{order.get('max_slippage_percent', '1')}% maximum slippage"),
        ]
        warnings = ["Liquidation risk applies to leveraged positions."]
        if mode == "Cross": warnings.append("Cross margin can use your other trading balance.")
        if _phase(phases, "SET_REFERRER"): warnings.append("This sets the displayed Hyperliquid referral before the order.")
        return _base("Open position", f"Open {market} {side}", f"{mode} · {leverage}x", rows, warnings, action, {})
    if kind == "CLOSE_POSITION":
        side = str(order.get("position_side", "position")).lower()
        size = _number(str(order.get("size_asset", "0")))
        before = _number(str(order.get("position_size_before_asset", "0")))
        portion = "100%" if size == before else f"{size} of {before} {market}"
        return _base("Close position", f"Close {market} {side}", "Reduce-only", [
            ("Amount to close", f"{portion} {market}"), ("Reference price", f"≈ {_number(str(order.get('reference_price', '0')))} USDC"),
            ("IOC price limit", f"{_number(str(order.get('limit_price', '0')))} USDC"),
            ("Price protection", f"{order.get('max_slippage_percent', '1')}% maximum slippage"),
        ], ["Reduce-only: this order cannot increase or reverse your position."], action, {})
    return _base("Hyperliquid action", "Review protected action", "Wallet confirmation required", [], [], action, {})


def result_presentation(result: Mapping[str, object], action: Mapping[str, object]) -> dict[str, object]:
    kind, status, code = (str(result.get(key, "")) for key in ("actionType", "status", "code"))
    base = action_presentation(action)
    funding = kind == "FUND_TRADING_ACCOUNT"
    if funding and status == "PENDING_CREDIT":
        title, subtitle = "Deposit sent", "Waiting for Hyperliquid balance update"
    elif funding and status == "FAILED":
        title, subtitle = "Deposit stopped", "Nothing was sent if the Wallet stopped before signing."
    elif status == "COMPLETED":
        title, subtitle = "Position order processed", "Check your updated position in PerpDEX."
    elif status == "PARTIAL":
        title, subtitle = "Position order partly filled", "Check the remaining position in PerpDEX."
    elif status == "UNKNOWN":
        title, subtitle = "Position order needs checking", "The external result could not be confirmed."
    else:
        title, subtitle = "Position order stopped", "Nothing was automatically retried."
    hash_value = next((str(item.get("publicId")) for item in result.get("phases", [])
                       if isinstance(item, Mapping) and str(item.get("publicId", "")).startswith("0x") and len(str(item.get("publicId"))) == 66), "")
    technical = list(base["technicalDetails"])
    technical.append({"label": "Result code", "value": code})
    if hash_value: technical.append({"label": "Arbitrum hash", "value": hash_value})
    return {**base, "resultTitle": title, "resultSubtitle": subtitle, "resultCode": code,
            "transactionHash": hash_value, "status": status, "technicalDetails": technical}


def operation_history_to_map(operation: Mapping[str, object]) -> dict[str, object]:
    kind = str(operation.get("action_type", ""))
    intent = operation.get("intent") if isinstance(operation.get("intent"), Mapping) else {}
    market, side = str(intent.get("market", "")), str(intent.get("side", "")).lower()
    title = {
        "OPEN_POSITION": f"Open {market} {side}",
        "CLOSE_POSITION": f"Close {market} position",
    }.get(kind, "Hyperliquid action")
    status = str(operation.get("state", "")).replace("_", " ").title()
    amount = str(intent.get("notional_usdc", ""))
    return {"actionId": str(operation.get("operation_id", "")), "actionType": kind,
            "summaryTitle": title, "counterpartyLabel": "Protocol", "shortRecipient": "Hyperliquid",
            "amountLabel": f"≤ {amount} USDC" if amount else "", "status": str(operation.get("state", "")).lower(),
            "statusLabel": status, "createdAt": str(operation.get("created_at", "")),
            "updatedAt": str(operation.get("updated_at", "")), "dateLabel": _date(str(operation.get("created_at", ""))),
            "isPerpDex": True, "operationId": str(operation.get("operation_id", "")), "token": "USDC",
            "transactionHash": "", "simulated": False, "isOperation": True}


def _base(label, title, subtitle, rows, warnings, action, extra):
    details = [("Operation ID", str(action.get("operationId", ""))), *extra.items()]
    return {"label": label, "title": title, "subtitle": subtitle,
            "summaryRows": [{"label": key, "value": value} for key, value in rows],
            "warnings": warnings, "technicalDetails": [{"label": key, "value": value} for key, value in details if value]}


def _phase(phases, phase_type):
    return next((dict(item.get("semantic", {})) for item in phases if isinstance(item, Mapping)
                 and item.get("phaseType") == phase_type and isinstance(item.get("semantic"), Mapping)), {})


def _number(value):
    try: return format(Decimal(value).normalize(), "f").rstrip("0").rstrip(".") or "0"
    except Exception: return "Unavailable"


def _usdc_atomic(value):
    try: return f"{_number(str(Decimal(value) / Decimal(1_000_000)))} USDC"
    except Exception: return "Unavailable"


def _eth(value):
    try: return f"≤ {_number(str(Decimal(value) / Decimal(10**18)))} ETH"
    except Exception: return "Unavailable"


def _divided(value, divisor):
    try: return _number(str(Decimal(value) / Decimal(str(divisor))))
    except Exception: return "Unavailable"


def _date(value):
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        return f"{parsed.strftime('%b')} {parsed.day}, {parsed.year}"
    except Exception: return "Recent activity"
