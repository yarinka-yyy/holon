"""Guard policy and lifecycle boundary for one exact Lending action intent."""

from __future__ import annotations

import hashlib
import json

from holon_contracts import MessageKind, RefusalCode, SecurityCode
from holon_guard_ipc import GuardState
from holon_journal import EventType
from holon_lending.preflight import parse_lending_intent
from .actions import ActionLedgerFailure
from .request_control import RequestControlFailure


def _ledger_failure(service, request, error: ActionLedgerFailure):
    if error.code == SecurityCode.ACTION_STATE_INVALID.value:
        return service.fail_closed_response(request, error.code)
    return service.refusal(request, error.code, "Action cannot be prepared.")


def _refuse_terminal(service, request, fingerprint: str, code: str):
    try:
        service.lifecycle.ledger.refuse(request.action_id or "", fingerprint, code)
    except ActionLedgerFailure as error:
        return _ledger_failure(service, request, error)
    return None


def prepare_lending_authority(service, request, owner_pid: int):
    try:
        intent = parse_lending_intent(request.payload)
    except Exception:
        return service.refusal(request, RefusalCode.REQUEST_INVALID.value, "Lending intent is invalid.")
    profile = service.lending_actions.select(intent.profile_id)
    if profile is None:
        return service.refusal(request, "ACTION_PROFILES_UNAVAILABLE", "Lending profile is unavailable.")
    existing = service.lifecycle.ledger.find(request.action_id or "")
    if existing is not None and intent.amount_mode == "all":
        return service.refusal(
            request, RefusalCode.ACTION_REPLAYED.value,
            "Action identifier was already used.",
        )
    material = {
        "schema": "lending-authority-1", "action": intent.action,
        "protocol_profile_id": intent.profile_id,
        "operation_id": request.action_id, "phase_action_id": request.action_id,
        "phase": "withdraw" if intent.action == "withdraw" else "approve_or_supply",
        "amount_mode": intent.amount_mode,
        "amount_atomic": (
            str(intent.amount_atomic) if intent.amount_atomic is not None else None
        ),
        "policy_revision": service.policy_snapshot.policy_revision,
        "policy_digest": service.policy_snapshot.policy_digest,
        "action_profile_digest": profile.digest,
    }
    request_fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    preliminary_payload = dict(request.payload)
    if intent.amount_atomic is not None:
        preliminary_payload["amount_atomic"] = intent.amount_atomic
    preliminary, rule = service.policy.evaluate_lending_intent(
        preliminary_payload, profile.digest,
    )
    builtin = service.policy.policy.schema_version == "4"
    if not preliminary.allowed or (rule is None and not builtin):
        failed = _refuse_terminal(
            service, request, request_fingerprint, preliminary.code,
        )
        return failed or service.refusal(
            request, preliminary.code, preliminary.message,
        )
    if intent.amount_atomic is not None:
        resolved_amount = intent.amount_atomic
    else:
        try:
            preview = service.lifecycle.wallet.preview_lending(request.payload, profile.digest)
            preview_payload = preview.payload if preview.ok else None
            if preview_payload is None:
                raise ValueError
            resolved_amount = int(str(preview_payload["amount_atomic"]))
            if (
                preview_payload.get("status") != "PREVIEW_READY"
                or preview_payload.get("requested_action") != intent.action
                or preview_payload.get("amount_mode") != intent.amount_mode
                or resolved_amount <= 0
            ):
                raise ValueError
        except Exception:
            code = "LENDING_PREFLIGHT_FAILED"
            failed = _refuse_terminal(
                service, request, request_fingerprint, code,
            )
            return failed or service.refusal(
                request, code, "Lending preflight is unavailable.",
            )
    material["amount_atomic"] = str(resolved_amount)
    fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        service.lifecycle.ledger.check_identity(request.action_id or "", fingerprint)
    except ActionLedgerFailure as error:
        return service.refusal(request, error.code, "Action cannot be prepared.")
    payload = dict(request.payload)
    payload["amount_atomic"] = resolved_amount
    decision, rule = service.policy.evaluate_lending_intent(payload, profile.digest)
    if not decision.allowed or (rule is None and not builtin):
        failed = _refuse_terminal(service, request, fingerprint, decision.code)
        return failed or service.refusal(request, decision.code, decision.message)
    if not service.audit_transfer(
        EventType.POLICY_DECISION, decision.code, request, policy_result="ALLOWED",
        canonical_amount_atomic=str(resolved_amount),
        canonical_policy_version=service.policy.policy.policy_version,
    ):
        return service.security_response(request)
    semantic_request = {
        "action_type": "lending", "network": "base",
        "recipient": "active_wallet_account", "asset": "usdc",
        "amount_atomic": str(resolved_amount),
    }
    try:
        control = service.audit.requests.observe(
            semantic_request, contract=profile.target,
            method=f"{intent.action}:{intent.amount_mode}",
        )
    except RequestControlFailure as error:
        return service.fail_closed_response(request, error.code)
    if control.expired and not service.audit_transfer(
        EventType.REQUEST_BLOCK_EXPIRED, "REQUEST_BLOCK_EXPIRED", request,
        canonical_amount_atomic=str(resolved_amount),
        canonical_policy_version=service.policy.policy.policy_version,
    ):
        return service.security_response(request)
    if control.blocked:
        code = RefusalCode.REQUEST_TEMPORARILY_BLOCKED.value
        failed = _refuse_terminal(service, request, fingerprint, code)
        if failed is not None:
            return failed
        if control.triggered:
            service.lifecycle.interrupt_for_security_block(code)
            audit_fields = {
                "guard_state": service.lifecycle.snapshot.state.value,
                "canonical_amount_atomic": str(resolved_amount),
                "canonical_policy_version": service.policy.policy.policy_version,
            }
            if not service.audit_transfer(
                EventType.REQUEST_BLOCK_STARTED, code, request, **audit_fields,
            ):
                return service.security_response(request)
            if not service.audit_transfer(
                EventType.RECOVERY_REQUIRED, code, request, **audit_fields,
            ):
                return service.security_response(request)
        return service.refusal(
            request, code, "Authority requests are temporarily blocked.",
        )
    result, prepared = service.lifecycle.start_lending_intent(
        owner_pid, request.action_id or "", fingerprint,
        {
            "action": intent.action, "amount_mode": intent.amount_mode,
            "amount": intent.amount, "protocol_profile_id": intent.profile_id,
        },
        resolved_amount,
        service.policy.policy.policy_version,
        None if rule is None else rule.max_total_fee_wei,
        None if rule is None else rule.max_amount_atomic,
        service.policy_snapshot.policy_revision, service.policy_snapshot.policy_digest,
        profile.digest,
        safety_digest=profile.safety_digest,
        phase=("withdraw" if intent.action == "withdraw" else "approve_or_supply"),
    )
    if not result.ok:
        if result.state is GuardState.RECOVERY_REQUIRED:
            return service._status(request, MessageKind.RECOVERY_REQUIRED, result.code)
        return service.refusal(request, result.code, result.message)
    if prepared is None:
        return service.error(request, "WALLET_UNAVAILABLE", "Wallet is unavailable.")
    if not service.audit_transfer(
        EventType.FLOW_STARTED, result.code, request,
        flow_id=result.flow_id, guard_state=GuardState.ACTIVE.value,
        canonical_amount_atomic=str(resolved_amount),
        canonical_policy_version=service.policy.policy.policy_version,
    ):
        service.lifecycle.interrupt_for_security_block("JOURNAL_WRITE_FAILED")
        return service.security_response(request)
    return service._status(request, MessageKind.PROTECTED_FLOW_STARTED, result.code)
