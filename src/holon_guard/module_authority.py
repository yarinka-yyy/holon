"""Guard boundary for one schema-bound optional-module protected action."""

from __future__ import annotations

import hashlib
import json

from holon_contracts import MessageKind, RefusalCode, SecurityCode
from holon_guard_ipc import GuardState
from holon_journal import EventType

from .actions import ActionLedgerFailure


_SAFE_PREPARE_CODES = frozenset({
    "HYPERLIQUID_UNAVAILABLE", "HYPERLIQUID_DATA_INVALID",
    "PERPDEX_NONCE_STATE_UNAVAILABLE", "PERPDEX_NONCE_STATE_INVALID",
    "PERPDEX_OPERATION_STATE_UNAVAILABLE", "PERPDEX_OPERATION_STATE_INVALID",
    "PERPDEX_PREVIEW_EXPIRED", "PERPDEX_PREVIEW_MISMATCH",
    "WALLET_ACCOUNT_UNAVAILABLE",
})
_SAFE_OPERATION_CLASSES = frozenset({
    "clearinghouseState", "frontendOpenOrders", "l2Book", "metaAndAssetCtxs",
    "orderStatus", "referral", "userFees", "userFillsByTime",
    "userNonFundingLedgerUpdates", "userVaultEquities", "vaultDetails",
})


def _prepare_failure(
    exc: Exception, *, fallback: str,
) -> tuple[str, str | None, str | None]:
    code = getattr(exc, "code", None)
    requested = getattr(exc, "operation_class", None)
    operation_class = requested if requested in _SAFE_OPERATION_CLASSES else None
    if isinstance(code, str) and (
        code in _SAFE_PREPARE_CODES or code.startswith("HYPERLIQUID_")
    ):
        if code == "HYPERLIQUID_UNAVAILABLE":
            return code, "public_transport", operation_class
        if code.startswith("HYPERLIQUID_"):
            return code, "public_data", operation_class
        if code.startswith("PERPDEX_"):
            return code, "perpdex_state", operation_class
        return code, "wallet", operation_class
    return (
        fallback,
        "adapter" if fallback == "MODULE_ADAPTER_UNAVAILABLE" else "internal",
        None,
    )


def _stage_failure_category(code: str) -> str:
    if code == "HYPERLIQUID_UNAVAILABLE":
        return "public_transport"
    if code.startswith("HYPERLIQUID_"):
        return "public_data"
    if code.startswith("PERPDEX_"):
        return "perpdex_state"
    if code.startswith("WALLET_"):
        return "wallet"
    return "internal"


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


def _refuse(
    service, request, fingerprint: str, code: str, message: str, *,
    stage: str | None = None, failure_category: str | None = None,
    operation_class: str | None = None,
):
    try:
        service.lifecycle.ledger.refuse(request.action_id or "", fingerprint, code)
    except ActionLedgerFailure as exc:
        if exc.code == SecurityCode.ACTION_STATE_INVALID.value:
            return service.fail_closed_response(request, exc.code)
        return service.refusal(request, exc.code, "Module action cannot be prepared.")
    if failure_category is not None:
        diagnostic = {"failure_category": failure_category}
        if stage is not None:
            diagnostic["stage"] = stage
        if operation_class in _SAFE_OPERATION_CLASSES:
            diagnostic["operation_class"] = operation_class
        if not service.audit_system(
            EventType.TECHNICAL_ERROR, code, action_id=request.action_id,
            **diagnostic,
        ):
            return service.security_response(request)
    return service.refusal(request, code, message, stage=stage)


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
    except Exception as exc:
        code, category, operation_class = _prepare_failure(
            exc, fallback="MODULE_ADAPTER_UNAVAILABLE",
        )
        return _refuse(
            service, request, fingerprint, code,
            "Module action adapter is unavailable.", stage="GUARD_FRESH_PREPARE",
            failure_category=category, operation_class=operation_class,
        )
    try:
        wallet = service.lifecycle.wallet.read_public_balances()
    except Exception:
        return _refuse(
            service, request, fingerprint, "WALLET_ACCOUNT_UNAVAILABLE",
            "Active Wallet account is unavailable.", stage="GUARD_FRESH_PREPARE",
            failure_category="wallet",
        )
    account = (
        wallet.payload.get("account")
        if wallet.ok and wallet.payload is not None else None
    )
    if not isinstance(account, dict):
        return _refuse(
            service, request, fingerprint, "WALLET_ACCOUNT_UNAVAILABLE",
            "Active Wallet account is unavailable.", stage="GUARD_FRESH_PREPARE",
            failure_category="wallet",
        )
    try:
        bundle = adapter.prepare(
            request.action_id or "", request.payload["action_type"],
            request.payload["params"], account, request.payload["preview_digest"],
        )
    except Exception as exc:
        code, category, operation_class = _prepare_failure(
            exc, fallback="MODULE_ACTION_INTERNAL_FAILURE",
        )
        return _refuse(
            service, request, fingerprint, code,
            "Module action could not be prepared from fresh public state.",
            stage="GUARD_FRESH_PREPARE", failure_category=category,
            operation_class=operation_class,
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
        operation_class = (
            prepared.get("operation_class") if isinstance(prepared, dict) else None
        )
        diagnostic = {
            "stage": result.stage,
            "failure_category": _stage_failure_category(result.code),
        }
        if operation_class in _SAFE_OPERATION_CLASSES:
            diagnostic["operation_class"] = operation_class
        if result.stage is not None and not service.audit_system(
            EventType.TECHNICAL_ERROR, result.code, action_id=request.action_id,
            **diagnostic,
        ):
            return service.security_response(request)
        return service._failure(request, result)
    if prepared is None:
        return service.error(
            request, "WALLET_UNAVAILABLE", "Wallet is unavailable.",
            stage="WALLET_PREPARE",
        )
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
