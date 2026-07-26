"""Guard policy and lifecycle boundary for one exact Aave supply intent."""

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
        if intent.action != "supply" or intent.amount_mode != "exact" or intent.amount_atomic is None:
            raise ValueError
    except Exception:
        return service.refusal(request, RefusalCode.REQUEST_INVALID.value, "Lending intent is invalid.")
    payload = dict(request.payload)
    payload["amount_atomic"] = intent.amount_atomic
    profile = service.lending_actions.profile
    if profile is None:
        return service.refusal(request, "ACTION_PROFILES_UNAVAILABLE", "Lending profile is unavailable.")
    decision, rule = service.policy.evaluate_lending_intent(payload, profile.digest)
    material = {
        "schema": "lending-authority-1", "action": "supply",
        "amount_atomic": str(intent.amount_atomic),
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
    if not decision.allowed or rule is None:
        try:
            service.lifecycle.ledger.refuse(request.action_id or "", fingerprint, decision.code)
        except ActionLedgerFailure:
            pass
        return service.refusal(request, decision.code, decision.message)
    if not service.audit_transfer(
        EventType.POLICY_DECISION, decision.code, request, policy_result="ALLOWED",
        canonical_amount_atomic=str(intent.amount_atomic),
        canonical_policy_version=service.policy.policy.policy_version,
    ):
        return service.security_response(request)
    result, prepared = service.lifecycle.start_lending_intent(
        owner_pid, request.action_id or "", fingerprint,
        {"action": "supply", "amount_mode": "exact", "amount": intent.amount or ""},
        service.policy.policy.policy_version, rule.max_total_fee_wei,
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
        canonical_amount_atomic=str(intent.amount_atomic),
        canonical_policy_version=service.policy.policy.policy_version,
    ):
        service.lifecycle.interrupt_for_security_block("JOURNAL_WRITE_FAILED")
        return service.security_response(request)
    return service._status(request, MessageKind.PROTECTED_FLOW_STARTED, result.code)
