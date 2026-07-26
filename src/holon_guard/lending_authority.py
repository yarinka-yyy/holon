"""Guard policy and lifecycle boundary for one exact Aave action intent."""

from __future__ import annotations

import hashlib
import json

from holon_contracts import MessageKind, RefusalCode
from holon_guard_ipc import GuardState
from holon_journal import EventType
from holon_lending.preflight import parse_lending_intent

from .actions import ActionLedgerFailure


def prepare_lending_authority(service, request, owner_pid: int):
    try:
        intent = parse_lending_intent(request.payload)
    except Exception:
        return service.refusal(request, RefusalCode.REQUEST_INVALID.value, "Lending intent is invalid.")
    profile = service.lending_actions.profile
    if profile is None:
        return service.refusal(request, "ACTION_PROFILES_UNAVAILABLE", "Lending profile is unavailable.")
    material = {
        "schema": "lending-authority-1", "action": intent.action,
        "amount_mode": intent.amount_mode,
        "amount_atomic": (
            str(intent.amount_atomic) if intent.amount_atomic is not None else None
        ),
        "policy_revision": service.policy_snapshot.policy_revision,
        "policy_digest": service.policy_snapshot.policy_digest,
        "action_profile_digest": profile.digest,
    }
    fingerprint = hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    try:
        service.lifecycle.ledger.check_identity(request.action_id or "", fingerprint)
    except ActionLedgerFailure as error:
        return service.refusal(request, error.code, "Action cannot be prepared.")
    preliminary_payload = dict(request.payload)
    if intent.amount_atomic is not None:
        preliminary_payload["amount_atomic"] = intent.amount_atomic
    preliminary, rule = service.policy.evaluate_lending_intent(
        preliminary_payload, profile.digest,
    )
    if not preliminary.allowed or rule is None:
        try:
            service.lifecycle.ledger.refuse(
                request.action_id or "", fingerprint, preliminary.code,
            )
        except ActionLedgerFailure:
            pass
        return service.refusal(request, preliminary.code, preliminary.message)
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
            try:
                service.lifecycle.ledger.refuse(
                    request.action_id or "", fingerprint, "LENDING_PREFLIGHT_FAILED",
                )
            except ActionLedgerFailure:
                pass
            return service.refusal(
                request, "LENDING_PREFLIGHT_FAILED", "Lending preflight is unavailable.",
            )
    payload = dict(request.payload)
    payload["amount_atomic"] = resolved_amount
    decision, rule = service.policy.evaluate_lending_intent(payload, profile.digest)
    if not decision.allowed or rule is None:
        try:
            service.lifecycle.ledger.refuse(request.action_id or "", fingerprint, decision.code)
        except ActionLedgerFailure:
            pass
        return service.refusal(request, decision.code, decision.message)
    if not service.audit_transfer(
        EventType.POLICY_DECISION, decision.code, request, policy_result="ALLOWED",
        canonical_amount_atomic=str(resolved_amount),
        canonical_policy_version=service.policy.policy.policy_version,
    ):
        return service.security_response(request)
    result, prepared = service.lifecycle.start_lending_intent(
        owner_pid, request.action_id or "", fingerprint,
        {
            "action": intent.action, "amount_mode": intent.amount_mode,
            "amount": intent.amount,
        },
        service.policy.policy.policy_version, rule.max_total_fee_wei,
        rule.max_amount_atomic,
        service.policy_snapshot.policy_revision, service.policy_snapshot.policy_digest,
        profile.digest,
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
