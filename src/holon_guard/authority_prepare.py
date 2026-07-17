from __future__ import annotations

from holon_contracts import MessageKind, RefusalCode, SecurityCode
from holon_guard_ipc import GuardState
from holon_journal import EventType, JournalFailure
from holon_policy import action_fingerprint

from .actions import ActionLedgerFailure
from .request_control import RequestControlFailure


def _ledger_failure(service, request, exc: ActionLedgerFailure):
    if exc.code == SecurityCode.ACTION_STATE_INVALID.value:
        return service.fail_closed_response(request, exc.code)
    if not service.audit_transfer(EventType.REFUSAL, exc.code, request):
        return service.security_response(request)
    return service.refusal(request, exc.code, "Action cannot be prepared.")


def _refuse_terminal(service, request, fingerprint: str, code: str):
    try:
        service.lifecycle.ledger.refuse(request.action_id or "", fingerprint, code)
    except ActionLedgerFailure as exc:
        return _ledger_failure(service, request, exc)
    if not service.audit_transfer(EventType.REFUSAL, code, request):
        return service.security_response(request)
    return None


def prepare(service, request, owner_pid: int):
    fingerprint = action_fingerprint(request)
    try:
        service.lifecycle.ledger.check_identity(request.action_id or "", fingerprint)
    except ActionLedgerFailure as exc:
        return _ledger_failure(service, request, exc)
    decision = service.policy.evaluate_transfer(request.payload)
    result_name = "ALLOWED" if decision.allowed else "REFUSED"
    if not service.audit_transfer(
        EventType.POLICY_DECISION, decision.code, request, policy_result=result_name
    ):
        return service.security_response(request)
    if not decision.allowed:
        failed = _refuse_terminal(service, request, fingerprint, decision.code)
        return failed or service.refusal(request, decision.code, decision.message)
    try:
        control = service.audit.requests.observe(request.payload)
    except RequestControlFailure as exc:
        return service.fail_closed_response(request, exc.code)
    if control.expired and not service.audit_transfer(
        EventType.REQUEST_BLOCK_EXPIRED, "REQUEST_BLOCK_EXPIRED", request
    ):
        return service.security_response(request)
    if control.blocked:
        code = RefusalCode.REQUEST_TEMPORARILY_BLOCKED.value
        failed = _refuse_terminal(service, request, fingerprint, code)
        if failed is not None:
            return failed
        if control.triggered:
            service.lifecycle.interrupt_for_security_block(code)
            if not service.audit_transfer(
                EventType.REQUEST_BLOCK_STARTED, code, request,
                guard_state=service.lifecycle.snapshot.state.value,
            ):
                return service.security_response(request)
            if not service.audit_transfer(
                EventType.RECOVERY_REQUIRED, code, request,
                guard_state=service.lifecycle.snapshot.state.value,
            ):
                return service.security_response(request)
        return service.refusal(request, code, "Authority requests are temporarily blocked.")
    result = service.lifecycle.start_flow(owner_pid, request.action_id or "", fingerprint)
    if result.code == RefusalCode.ACTION_ALREADY_ACTIVE.value:
        failed = _refuse_terminal(service, request, fingerprint, result.code)
        if failed is not None:
            return failed
    if not result.ok:
        event_type = (
            EventType.RECOVERY_REQUIRED
            if result.state is GuardState.RECOVERY_REQUIRED
            else EventType.REFUSAL if result.code in service.refusal_codes
            else EventType.TECHNICAL_ERROR
        )
        extra = {"guard_state": result.state.value} if event_type is EventType.RECOVERY_REQUIRED else {}
        if not service.audit_transfer(
            event_type, result.code, request, **extra,
        ):
            return service.security_response(request)
        return service._failure(request, result)
    if not service.audit_transfer(
        EventType.FLOW_STARTED, result.code, request,
        flow_id=result.flow_id, guard_state=GuardState.ACTIVE.value,
    ):
        return service.security_response(request)
    return service._status(request, MessageKind.PROTECTED_FLOW_STARTED, result.code)
