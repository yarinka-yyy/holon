"""Guard boundary for one schema-bound optional-module protected action."""

from __future__ import annotations

import hashlib
import json

from holon_contracts import MessageKind, RefusalCode, SecurityCode
from holon_guard_ipc import GuardState
from holon_journal import EventType

from .actions import ActionLedgerFailure


def _fingerprint(request) -> str:
    material = {
        "schema": "module-authority-1",
        "module_id": request.payload["module_id"],
        "capability_id": request.payload["capability_id"],
        "action_type": request.payload["action_type"],
        "params": request.payload["params"],
        "preview_digest": request.payload["preview_digest"],
    }
    return hashlib.sha256(
        json.dumps(material, ensure_ascii=True, separators=(",", ":"), sort_keys=True).encode()
    ).hexdigest()


def _refuse(service, request, fingerprint: str, code: str, message: str):
    try:
        service.lifecycle.ledger.refuse(request.action_id or "", fingerprint, code)
    except ActionLedgerFailure as exc:
        if exc.code == SecurityCode.ACTION_STATE_INVALID.value:
            return service.fail_closed_response(request, exc.code)
        return service.refusal(request, exc.code, "Module action cannot be prepared.")
    return service.refusal(request, code, message)


def prepare_module_authority(service, request, owner_pid: int):
    fingerprint = _fingerprint(request)
    try:
        service.lifecycle.ledger.check_identity(request.action_id or "", fingerprint)
    except ActionLedgerFailure as exc:
        return service.refusal(request, exc.code, "Module action cannot be prepared.")
    try:
        capability, adapter = service.module_action_adapter(
            str(request.payload["module_id"]),
            str(request.payload["capability_id"]),
            str(request.payload["action_type"]),
        )
        wallet = service.lifecycle.wallet.read_public_balances()
        account = (
            wallet.payload.get("account")
            if wallet.ok and wallet.payload is not None else None
        )
        if not isinstance(account, dict):
            raise RuntimeError("Wallet account is unavailable")
        bundle = adapter.prepare(
            request.action_id or "", request.payload["action_type"],
            request.payload["params"], account, request.payload["preview_digest"],
        )
    except Exception as exc:
        code = str(getattr(exc, "code", "MODULE_ACTION_UNAVAILABLE"))
        if not code or len(code) > 64:
            code = "MODULE_ACTION_UNAVAILABLE"
        return _refuse(
            service, request, fingerprint, code,
            "Module action could not be prepared from fresh public state.",
        )
    if service.lifecycle.snapshot.state is GuardState.RECOVERY_REQUIRED:
        previous_action_id = service.lifecycle.snapshot.action_id
        released = service.lifecycle.release_module_recovery_for_exit(
            str(request.payload["action_type"]),
        )
        if not released.ok:
            try:
                adapter.reject(request.action_id or "")
            except Exception:
                pass
            return service._failure(request, released)
        if not service.audit_system(
            EventType.RECOVERY_COMPLETED, released.code,
            action_id=previous_action_id,
            guard_state=GuardState.NORMAL.value,
        ):
            try:
                adapter.reject(request.action_id or "")
            except Exception:
                pass
            return service.security_response(request)
    result, prepared = service.lifecycle.start_module_intent(
        owner_pid, request.action_id or "", fingerprint,
        capability.module_id, str(getattr(adapter, "wallet_capability_id", "")),
        str(capability.declaration.descriptor["profile_id"]),
        str(request.payload["action_type"]), bundle.to_mapping(),
    )
    if not result.ok:
        try:
            adapter.reject(request.action_id or "")
        except Exception:
            pass
        return service._failure(request, result)
    if prepared is None:
        return service.error(request, "WALLET_UNAVAILABLE", "Wallet is unavailable.")
    try:
        adapter.mark_awaiting_confirmation(request.action_id or "")
    except Exception:
        service.lifecycle.interrupt_for_security_block("PERPDEX_OPERATION_STATE_INVALID")
        return service.security_response(request)
    if not service.audit_system(
        EventType.FLOW_STARTED, result.code,
        action_id=request.action_id, flow_id=result.flow_id,
        guard_state=GuardState.ACTIVE.value,
        action_type=str(request.payload["action_type"]),
        wallet_address=str(bundle.account),
    ):
        service.lifecycle.interrupt_for_security_block("JOURNAL_WRITE_FAILED")
        return service.security_response(request)
    return service._status(request, MessageKind.PROTECTED_FLOW_STARTED, result.code)
