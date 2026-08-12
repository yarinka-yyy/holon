"""Secret-free, human-readable presentation for protected PerpDEX actions."""

from __future__ import annotations

from decimal import Decimal
from datetime import UTC, datetime
from pathlib import Path
from typing import Mapping

from holon_guard.action_store import ActionStateStore
from holon_journal import EventType, JournalStore


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
    elif code == "PERPDEX_PRICE_MOVED":
        title = "Price changed before signing"
        subtitle = "Order was not sent. Review a fresh quote to try again."
    elif code == "HYPERLIQUID_ACTION_REJECTED":
        title, subtitle = "Hyperliquid rejected the action", "No phase was automatically retried."
    elif code in {"IOC_NOT_FILLED", "IOC_PARTIAL_FILL"}:
        title = "IOC order was not fully filled"
        subtitle = "Check the current position before creating another order."
    elif code in {"HYPERLIQUID_RESULT_UNKNOWN", "PERPDEX_RESULT_UNKNOWN"}:
        title = "Order result needs checking"
        subtitle = "Do not repeat it until the current position and order history are verified."
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
    stage = result.get("terminalStage")
    if stage: technical.append({"label": "Failure stage", "value": str(stage)})
    submitted = result.get("externalSubmissionStarted") is True
    technical.append({
        "label": "External submission",
        "value": "Started" if submitted else "Not attempted",
    })
    if not submitted and (funding or code == "PERPDEX_PRICE_MOVED"):
        technical.append({
            "label": "Signature",
            "value": "Created locally; not sent" if funding and hash_value else "Not created",
        })
    if hash_value: technical.append({"label": "Arbitrum hash", "value": hash_value})
    return {**base, "resultTitle": title, "resultSubtitle": subtitle, "resultCode": code,
            "transactionHash": hash_value, "status": status, "technicalDetails": technical}


def load_action_diagnostics(
    data_dir: Path, action_ids: set[str],
) -> dict[str, dict[str, object]]:
    """Join strict local Guard evidence for Wallet presentation only."""
    values = {action_id: {} for action_id in action_ids}
    try:
        snapshot = ActionStateStore(Path(data_dir) / "action-state.json").load()
        records = (() if snapshot.current is None else (snapshot.current,)) + snapshot.terminal
        for record in records:
            if record.action_id in values:
                values[record.action_id].update({
                    "action_state": record.state.value,
                    "result_code": record.code,
                })
    except Exception:
        pass
    try:
        for event in JournalStore(Path(data_dir) / "journal.jsonl").read_events():
            action_id = event.public_fields.get("action_id")
            if action_id not in values:
                continue
            item = values[str(action_id)]
            if event.event_type is EventType.TECHNICAL_ERROR:
                item["result_code"] = event.code
                for field in (
                    "stage", "failure_category", "operation_class", "ipc_outcome",
                ):
                    if field in event.public_fields:
                        item[field] = event.public_fields[field]
            elif event.event_type is EventType.RECOVERY_COMPLETED:
                item["recovery_state"] = "COMPLETED"
    except Exception:
        pass
    for item in values.values():
        code = str(item.get("result_code", ""))
        item.setdefault("stage", _stage_for_code(code))
        item.setdefault("failure_category", _category_for_code(code))
        if code == "WALLET_PREPARATION_AMBIGUOUS":
            item.setdefault("ipc_outcome", "UNKNOWN")
        item.setdefault(
            "recovery_state",
            "REQUIRED" if item.get("action_state") == "RECOVERY_REQUIRED" else "NOT_REQUIRED",
        )
    return values


def funding_history_to_map(
    base: Mapping[str, object], operation: Mapping[str, object] | None = None,
    diagnostic: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Join public EVM funding history with secret-free PerpDEX terminal evidence."""
    operation, diagnostic = operation or {}, diagnostic or {}
    intent = operation.get("intent") if isinstance(operation.get("intent"), Mapping) else {}
    operation_id = str(
        operation.get("operation_id") or base.get("operationId") or base.get("actionId") or ""
    )
    amount = str(base.get("amount") or (
        f"{intent.get('amount_usdc')} USDC" if intent.get("amount_usdc") else ""
    ))
    code = str(operation.get("terminal_code") or diagnostic.get("result_code") or "")
    stage = str(operation.get("terminal_stage") or diagnostic.get("stage") or "")
    category = str(
        operation.get("failure_category") or diagnostic.get("failure_category") or ""
    )
    external_started = (
        operation.get("external_submission_started") is True
        if operation
        else str(base.get("status", "")).lower() in {"pending", "confirmed"}
    )
    state = str(operation.get("state") or base.get("status") or "").upper()
    status = state.replace("_", " ").title() or "Unavailable"
    explanation = _result_explanation(code, str(diagnostic.get("recovery_state", "NOT_REQUIRED")))
    if not explanation and code.startswith("FUNDING_"):
        explanation = (
            "The deposit did not complete. Review the safe result code and submission state "
            "before creating another action."
        )
    signature = (
        "Created before external attempt" if external_started
        else "Created locally; not sent" if base.get("transactionHash")
        else "Not created"
    )
    rows = [
        ("Status", status), ("Action", "Deposit to Hyperliquid"),
        ("Amount", amount or "Unavailable"),
        ("Network", str(base.get("networkLabel") or "Arbitrum One")),
        ("Wallet", str(base.get("sender") or operation.get("account") or "Unavailable")),
        ("Destination", "Hyperliquid trading balance"),
        ("Result code", code or "Unavailable"),
        ("Failure stage", stage or "Unavailable"), ("Signature", signature),
        ("External submission", "Started" if external_started else "Not attempted"),
        ("Updated", str(base.get("updatedAt") or operation.get("updated_at") or "Unavailable")),
    ]
    technical = [
        ("Operation ID", operation_id), ("Result code", code),
        ("Failure stage", stage), ("Failure category", category),
        ("External submission", "Started" if external_started else "Not attempted"),
        ("Arbitrum hash", str(base.get("transactionHash") or "")),
        ("Native USDC contract", str(base.get("contract") or "")),
        ("Hyperliquid Bridge2", str(base.get("recipient") or "")),
    ]
    phases = operation.get("phases") if isinstance(operation.get("phases"), list) else []
    for phase in phases:
        if not isinstance(phase, Mapping):
            continue
        value = str(phase.get("state", "Unavailable"))
        if phase.get("code"): value += " · " + str(phase["code"])
        if phase.get("public_id"): value += " · " + str(phase["public_id"])
        technical.append((str(phase.get("phase_type", "Phase")), value))
    diagnostics = [
        ("Action ID", str(base.get("actionId") or operation_id)),
        ("Operation ID", operation_id), ("Action", "FUND_TRADING_ACCOUNT"),
        ("Amount", amount or "Unavailable"),
        ("Wallet", str(base.get("sender") or operation.get("account") or "Unavailable")),
        ("Status", state or "Unavailable"), ("Result code", code or "Unavailable"),
        ("Failure stage", stage or "Unavailable"),
        ("Failure category", category or "Unavailable"), ("Signature", signature),
        ("External submission started", str(external_started).lower()),
        ("Arbitrum hash", str(base.get("transactionHash") or "Unavailable")),
        ("Reason", explanation or "No additional terminal explanation is available."),
    ]
    return {
        **base,
        "actionId": str(base.get("actionId") or operation_id),
        "operationId": operation_id,
        "actionType": "FUND_TRADING_ACCOUNT",
        "summaryTitle": "Deposit to Hyperliquid",
        "counterpartyLabel": "Protocol", "shortRecipient": "Hyperliquid",
        "amount": amount, "amountLabel": amount,
        "status": state.lower(), "statusLabel": status,
        "createdAt": str(base.get("createdAt") or operation.get("created_at") or ""),
        "updatedAt": str(base.get("updatedAt") or operation.get("updated_at") or ""),
        "dateLabel": str(base.get("dateLabel") or _date(str(operation.get("created_at", "")))),
        "isPerpDex": True, "isOperation": not bool(base), "token": "USDC",
        "transactionHash": str(base.get("transactionHash") or ""), "simulated": False,
        "detailRows": [{"label": label, "value": value} for label, value in rows],
        "technicalDetails": [
            {"label": label, "value": value} for label, value in technical if value
        ],
        "diagnosticsText": "\n".join(f"{label}: {value}" for label, value in diagnostics),
        "resultExplanation": explanation,
        "externalSubmissionStarted": external_started,
    }


def operation_history_to_map(
    operation: Mapping[str, object], diagnostic: Mapping[str, object] | None = None,
) -> dict[str, object]:
    diagnostic = diagnostic or {}
    kind = str(operation.get("action_type", ""))
    if kind == "FUND_TRADING_ACCOUNT":
        return funding_history_to_map({}, operation, diagnostic)
    intent = operation.get("intent") if isinstance(operation.get("intent"), Mapping) else {}
    market, side = str(intent.get("market", "")), str(intent.get("side", "")).lower()
    title = {
        "OPEN_POSITION": f"Open {market} {side}",
        "CLOSE_POSITION": f"Close {market} position",
    }.get(kind, "Hyperliquid action")
    state = str(operation.get("state", ""))
    status = state.replace("_", " ").title()
    amount = str(intent.get("notional_usdc", ""))
    leverage = intent.get("leverage")
    margin = _divided(amount, leverage) if amount and leverage else ""
    code = str(operation.get("terminal_code") or diagnostic.get("result_code") or "")
    stage = str(operation.get("terminal_stage") or diagnostic.get("stage") or "")
    category = str(operation.get("failure_category") or diagnostic.get("failure_category") or "")
    operation_class = str(operation.get("operation_class") or diagnostic.get("operation_class") or "")
    external_started = operation.get("external_submission_started") is True
    recovery = str(diagnostic.get("recovery_state", "NOT_REQUIRED"))
    signature = (
        "Not created" if not external_started and stage in {
            "WALLET_LIVE_VERIFY", "WALLET_EXECUTION_PRE_VERIFY", "WALLET_AUTHENTICATION",
            "WALLET_PREPARE",
        } else "Created before external attempt" if external_started
        else "No external signature recorded"
    )
    explanation = _result_explanation(code, recovery)
    if code == "PERPDEX_PRICE_MOVED":
        status = "Failed · price changed before signing"
    elif code == "WALLET_PREPARATION_AMBIGUOUS":
        status = "Failed · Wallet response was not validated"
    rows = [
        ("Status", status or "Unavailable"),
        ("Action", kind.replace("_", " ").title()),
        ("Market", market or "Unavailable"),
        ("Side", side.upper() if side else "Unavailable"),
        ("Margin", f"≈ {margin} USDC" if margin else "Unavailable"),
        ("Maximum position", f"≤ {amount} USDC" if amount else "Unavailable"),
        ("Margin mode", str(intent.get("margin_mode", "Unavailable")).title()),
        ("Leverage", f"{leverage}x" if leverage else "Unavailable"),
        ("Wallet", str(operation.get("account", "Unavailable"))),
        ("Protocol", "Hyperliquid"),
        ("Result code", code or "Unavailable"),
        ("Failure stage", stage or "Unavailable"),
        ("Signature", signature),
        ("External submission", "Started" if external_started else "Not attempted"),
        ("Recovery", _recovery_label(recovery)),
        ("Updated", str(operation.get("updated_at", "Unavailable"))),
    ]
    phases = operation.get("phases") if isinstance(operation.get("phases"), list) else []
    technical = [
        ("Operation ID", str(operation.get("operation_id", ""))),
        ("Result code", code), ("Failure stage", stage),
        ("Failure category", category), ("Hyperliquid read", operation_class),
        ("IPC outcome", str(diagnostic.get("ipc_outcome", ""))),
        ("External submission", "Started" if external_started else "Not attempted"),
    ]
    for phase in phases:
        if not isinstance(phase, Mapping):
            continue
        phase_value = str(phase.get("state", "Unavailable"))
        if phase.get("code"): phase_value += " · " + str(phase["code"])
        if phase.get("public_id"): phase_value += " · " + str(phase["public_id"])
        technical.append((str(phase.get("phase_type", "Phase")), phase_value))
    diagnostic_items = [
        ("Action ID", str(operation.get("operation_id", ""))),
        ("Action", kind), ("Market", market), ("Side", side.upper()),
        ("Margin", f"{margin} USDC" if margin else "Unavailable"),
        ("Notional", f"{amount} USDC" if amount else "Unavailable"),
        ("Leverage", f"{leverage}x" if leverage else "Unavailable"),
        ("Wallet", str(operation.get("account", "Unavailable"))),
        ("Status", state), ("Result code", code or "Unavailable"),
        ("Failure stage", stage or "Unavailable"),
        ("Failure category", category or "Unavailable"),
        ("Hyperliquid read", operation_class or "Unavailable"),
        ("IPC outcome", str(diagnostic.get("ipc_outcome") or "Unavailable")),
        ("Signature", signature),
        ("External submission started", str(external_started).lower()),
        ("Recovery", _recovery_label(recovery)),
        ("Reason", explanation),
    ]
    for phase in phases:
        if not isinstance(phase, Mapping):
            continue
        phase_value = str(phase.get("state", "Unavailable"))
        if phase.get("code"): phase_value += " · " + str(phase["code"])
        if phase.get("public_id"): phase_value += " · " + str(phase["public_id"])
        diagnostic_items.append((str(phase.get("phase_type", "Phase")), phase_value))
    diagnostics_text = "\n".join(
        f"{label}: {value}" for label, value in diagnostic_items
    )
    return {"actionId": str(operation.get("operation_id", "")), "actionType": kind,
            "summaryTitle": title, "counterpartyLabel": "Protocol", "shortRecipient": "Hyperliquid",
            "amount": f"≤ {amount} USDC" if amount else "", "amountLabel": f"≤ {amount} USDC" if amount else "",
            "status": state.lower(),
            "statusLabel": status, "createdAt": str(operation.get("created_at", "")),
            "updatedAt": str(operation.get("updated_at", "")), "dateLabel": _date(str(operation.get("created_at", ""))),
            "isPerpDex": True, "operationId": str(operation.get("operation_id", "")), "token": "USDC",
            "transactionHash": "", "simulated": False, "isOperation": True,
            "detailRows": [{"label": label, "value": value} for label, value in rows],
            "technicalDetails": [
                {"label": label, "value": value} for label, value in technical if value
            ],
            "diagnosticsText": diagnostics_text, "resultExplanation": explanation,
            "externalSubmissionStarted": external_started}


def _stage_for_code(code: str) -> str:
    if code in {"PERPDEX_PRICE_MOVED", "PERPDEX_POSITION_CHANGED", "HYPERLIQUID_UNAVAILABLE"}:
        return "WALLET_EXECUTION_PRE_VERIFY"
    if code == "AUTHENTICATION_FAILED": return "WALLET_AUTHENTICATION"
    if code == "WALLET_PREPARATION_AMBIGUOUS": return "WALLET_PREPARE"
    if code in {"HYPERLIQUID_ACTION_REJECTED", "HYPERLIQUID_RESULT_UNKNOWN"}:
        return "RECONCILIATION"
    return ""


def _category_for_code(code: str) -> str:
    if code == "HYPERLIQUID_UNAVAILABLE": return "public_transport"
    if code == "WALLET_PREPARATION_AMBIGUOUS": return "wallet_ipc"
    if code == "AUTHENTICATION_FAILED": return "authentication"
    if code == "HYPERLIQUID_ACTION_REJECTED": return "exchange_rejected"
    if code in {"HYPERLIQUID_RESULT_UNKNOWN", "PERPDEX_RESULT_UNKNOWN"}: return "exchange_unknown"
    if code.startswith("PERPDEX_"): return "perpdex_state"
    return ""


def _result_explanation(code: str, recovery: str) -> str:
    values = {
        "FUNDING_BROADCAST_PENDING": "The Arbitrum transfer was sent. Hyperliquid credit is still pending.",
        "FUNDING_RESULT_UNKNOWN": "The external funding result could not be confirmed. Do not repeat it until the public transaction state is checked.",
        "FUNDING_REVALIDATION_FAILED": "The deposit was stopped before signing because fresh Wallet checks no longer matched the reviewed action.",
        "FUNDING_AUTHENTICATION_FAILED": "The deposit was stopped before signing because local Wallet authentication failed.",
        "FUNDING_HISTORY_UNAVAILABLE": "The Wallet could not safely persist the funding result. No automatic retry was made.",
        "PERPDEX_PRICE_MOVED": "Price moved outside the protected limit before signing. The order was not sent.",
        "HYPERLIQUID_ACTION_REJECTED": "Hyperliquid rejected the submitted action.",
        "IOC_NOT_FILLED": "The IOC order was submitted but did not fill.",
        "IOC_PARTIAL_FILL": "The IOC order filled only partially.",
        "HYPERLIQUID_RESULT_UNKNOWN": "Submission started, but the external result could not be confirmed.",
        "WALLET_PREPARATION_AMBIGUOUS": "The Wallet preparation response could not be safely validated. No action was retried.",
        "HYPERLIQUID_UNAVAILABLE": "Required public Hyperliquid data was unavailable.",
    }
    value = values.get(code, "No action was automatically retried.")
    if recovery == "COMPLETED":
        value += " Guard recovery completed; the original action was not resumed."
    return value


def _recovery_label(value: str) -> str:
    return {
        "COMPLETED": "Completed · original action not resumed",
        "REQUIRED": "Required before a new protected action",
        "NOT_REQUIRED": "Not required",
    }.get(value, "Unavailable")


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
