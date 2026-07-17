"""Contract, policy, replay, and Guard lifecycle boundary."""

from __future__ import annotations

from holon_contracts import (
    ActionState, ContractEnvelope, MessageKind, RefusalCode, SecurityCode, make_envelope,
)
from holon_guard_ipc import GuardState
from holon_policy import PolicyEngine, action_fingerprint

from .lifecycle import GuardLifecycle

REFUSAL_CODES = frozenset(item.value for item in RefusalCode)
SAFE_HEALTH_CODES = frozenset(
    {"OK", "STATE_MISSING", "STATE_INVALID", "STATE_WRITE_FAILED"}
    | {item.value for item in SecurityCode}
    | {RefusalCode.POLICY_AUTHORITY_DISABLED.value}
)


class AuthorityService:
    def __init__(self, lifecycle: GuardLifecycle, policy: PolicyEngine) -> None:
        self.lifecycle = lifecycle
        self.policy = policy

    def _response(
        self, request: ContractEnvelope, kind: MessageKind, payload: dict
    ) -> ContractEnvelope:
        return make_envelope(
            kind, payload, request_id=request.request_id, action_id=request.action_id
        )

    def refusal(self, request: ContractEnvelope, code: str, message: str) -> ContractEnvelope:
        return self._response(
            request, MessageKind.REFUSAL,
            {"code": code, "message": message, "retryable": False},
        )

    def error(self, request: ContractEnvelope, code: str, message: str) -> ContractEnvelope:
        return self._response(
            request, MessageKind.ERROR,
            {"code": code, "message": message, "retryable": False},
        )

    def _failure(self, request: ContractEnvelope, result) -> ContractEnvelope:
        if result.state is GuardState.SIGNING_DISABLED:
            return self._response(
                request, MessageKind.SIGNING_DISABLED,
                {"guard_state": result.state.value, "authority_available": False,
                 "code": result.code, "message": result.message},
            )
        if result.state is GuardState.RECOVERY_REQUIRED:
            return self._status(request, MessageKind.RECOVERY_REQUIRED, result.code)
        if result.code == "ACTION_ID_MISMATCH":
            return self.refusal(
                request, RefusalCode.ACTION_ID_INVALID.value,
                "Action identifier does not match.",
            )
        if result.code in REFUSAL_CODES:
            return self.refusal(request, result.code, result.message)
        return self.error(request, result.code, result.message)

    def _status(self, request: ContractEnvelope, kind: MessageKind, code: str) -> ContractEnvelope:
        record = self.lifecycle.ledger.find(request.action_id or "")
        if record is None:
            return self.refusal(request, RefusalCode.ACTION_ID_INVALID.value, "Action was not found.")
        snapshot = self.lifecycle.snapshot
        flow_id = snapshot.flow_id if snapshot.action_id == record.action_id else None
        payload = {
            "guard_state": snapshot.state.value,
            "action_state": record.state.value,
            "flow_id": flow_id,
            "code": code,
            "message": "Action status is available.",
        }
        return self._response(request, kind, payload)

    def _health(self, request: ContractEnvelope) -> ContractEnvelope:
        result = self.lifecycle.health()
        code = result.code if result.code in SAFE_HEALTH_CODES else "SIGNING_DISABLED"
        return self._response(
            request,
            MessageKind.HEALTH_RESPONSE,
            {
                "guard_state": result.state.value,
                "authority_available": False,
                "code": code,
                "message": result.message,
                "compatibility": "COMPATIBLE",
            },
        )

    def _prepare(self, request: ContractEnvelope, owner_pid: int) -> ContractEnvelope:
        decision = self.policy.evaluate_transfer(request.payload)
        if not decision.allowed:
            return self.refusal(request, decision.code, decision.message)
        result = self.lifecycle.start_flow(
            owner_pid, request.action_id or "", action_fingerprint(request)
        )
        if result.ok:
            return self._status(request, MessageKind.PROTECTED_FLOW_STARTED, result.code)
        return self._failure(request, result)

    def handle(self, request: ContractEnvelope, owner_pid: int | None) -> ContractEnvelope:
        if request.kind is MessageKind.HEALTH_REQUEST:
            return self._health(request)
        if request.kind is MessageKind.PREPARE_TRANSFER:
            assert owner_pid is not None
            return self._prepare(request, owner_pid)
        if request.kind is MessageKind.ACTION_STATUS_REQUEST:
            return self._status(request, MessageKind.ACTION_STATUS, "ACTION_STATUS")
        if request.kind is MessageKind.CANCEL_ACTION:
            result = self.lifecycle.cancel_flow(request.action_id or "")
            if not result.ok:
                return self._failure(request, result)
            return self._status(request, MessageKind.ACTION_STATUS, result.code)
        result = self.lifecycle.recover_flow(request.action_id or "")
        if not result.ok:
            return self._failure(request, result)
        return self._status(request, MessageKind.ACTION_STATUS, result.code)
