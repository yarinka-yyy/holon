"""Guard preparation for exact human-readable Wallet transfer intents."""

from __future__ import annotations

from web3 import Web3

from holon_contracts import MessageKind, RefusalCode, SecurityCode
from holon_guard_ipc import GuardState
from holon_journal import EventType
from holon_wallet.public_data import BASE_USDC, ETHEREUM_USDC
from holon_wallet.transfer import parse_transfer_amount, transfer_route

from .actions import ActionLedgerFailure
from .request_control import RequestControlFailure
from .semantic import intent_fingerprint

RESERVED = frozenset({"0x" + "00" * 20, BASE_USDC.lower(), ETHEREUM_USDC.lower()})


def _canonical_recipient(value: str) -> str:
    body = value[2:]
    if not (body.islower() or body.isupper()) and not Web3.is_checksum_address(value):
        raise ValueError
    normalized = Web3.to_checksum_address(value)
    if normalized.lower() in RESERVED:
        raise ValueError
    return normalized


def _refuse(service, request, fingerprint: str, code: str, message: str):
    try:
        service.lifecycle.ledger.refuse(request.action_id or "", fingerprint, code)
    except ActionLedgerFailure as error:
        if error.code == SecurityCode.ACTION_STATE_INVALID.value:
            return service.fail_closed_response(request, error.code)
        return service.refusal(request, error.code, "Action cannot be prepared.")
    return service.refusal(request, code, message)


def prepare_intent(service, request, owner_pid: int):
    payload = request.payload
    try:
        route = transfer_route(payload["network"], payload["asset"])
        amount_atomic, _ = parse_transfer_amount(payload["amount"], route.decimals)
        recipient = _canonical_recipient(payload["recipient"])
    except Exception:
        fallback = "0" * 64
        return _refuse(
            service, request, fallback, RefusalCode.REQUEST_INVALID.value,
            "Transfer intent is invalid.",
        )
    decision, rule = service.policy.evaluate_intent(
        route.network_id, route.asset_id, amount_atomic,
    )
    fee_cap = "1" if rule is None else rule.max_total_fee_wei
    fingerprint = intent_fingerprint(
        policy_version=service.policy.policy.policy_version,
        network=route.network_id,
        asset=route.asset_id,
        amount_atomic=str(amount_atomic),
        recipient=recipient,
        max_total_fee_wei=fee_cap,
    )
    audit_fields = {
        "canonical_amount_atomic": str(amount_atomic),
        "canonical_policy_version": service.policy.policy.policy_version,
    }
    try:
        service.lifecycle.ledger.check_identity(request.action_id or "", fingerprint)
    except ActionLedgerFailure as error:
        return service.refusal(request, error.code, "Action cannot be prepared.")
    if not service.audit_transfer(
        EventType.POLICY_DECISION, decision.code, request,
        policy_result="ALLOWED" if decision.allowed else "REFUSED", **audit_fields,
    ):
        return service.security_response(request)
    if not decision.allowed or rule is None:
        return _refuse(service, request, fingerprint, decision.code, decision.message)
    canonical = {
        "action_type": "transfer",
        "network": route.network_id,
        "asset": route.asset_id,
        "amount_atomic": str(amount_atomic),
        "recipient": recipient,
    }
    try:
        control = service.audit.requests.observe(canonical)
    except RequestControlFailure as error:
        return service.fail_closed_response(request, error.code)
    if control.blocked:
        code = RefusalCode.REQUEST_TEMPORARILY_BLOCKED.value
        if control.triggered:
            service.lifecycle.interrupt_for_security_block(code)
        return _refuse(
            service, request, fingerprint, code,
            "Authority requests are temporarily blocked.",
        )
    result, prepared = service.lifecycle.start_transfer_intent(
        owner_pid,
        request.action_id or "",
        fingerprint,
        {
            "network": route.network_id,
            "asset": route.asset_id,
            "amount_atomic": str(amount_atomic),
            "recipient": recipient,
        },
        rule.max_total_fee_wei,
    )
    if not result.ok:
        if result.state is GuardState.RECOVERY_REQUIRED:
            return service._status(request, MessageKind.RECOVERY_REQUIRED, result.code)
        if result.code in service.refusal_codes:
            return service.refusal(request, result.code, result.message)
        return service.error(request, result.code, result.message)
    if prepared is None:
        return service.error(request, "WALLET_UNAVAILABLE", "Wallet is unavailable.")
    if not service.audit_transfer(
        EventType.FLOW_STARTED, result.code, request,
        flow_id=result.flow_id, guard_state=GuardState.ACTIVE.value, **audit_fields,
    ):
        return service.security_response(request)
    return service._status(request, MessageKind.PROTECTED_FLOW_STARTED, result.code)
