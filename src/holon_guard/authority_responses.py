from __future__ import annotations

from holon_contracts import ContractEnvelope, MessageKind, RefusalCode, SecurityCode, make_envelope
from holon_guard_ipc import GuardState
from holon_journal import EventType

REFUSAL_CODES = frozenset(item.value for item in RefusalCode)
SAFE_HEALTH_CODES = frozenset(
    {"OK", "STATE_MISSING", "STATE_INVALID", "STATE_WRITE_FAILED"}
    | {item.value for item in SecurityCode}
    | {RefusalCode.POLICY_AUTHORITY_DISABLED.value}
)


class ResponseMixin:
    def _response(
        self, request: ContractEnvelope, kind: MessageKind, payload: dict
    ) -> ContractEnvelope:
        return make_envelope(
            kind, payload, request_id=request.request_id, action_id=request.action_id
        )

    def refusal(
        self, request: ContractEnvelope, code: str, message: str, *,
        stage: str | None = None,
    ) -> ContractEnvelope:
        payload = {"code": code, "message": message, "retryable": False}
        if stage is not None:
            payload["stage"] = stage
        return self._response(
            request, MessageKind.REFUSAL,
            payload,
        )

    def error(
        self, request: ContractEnvelope, code: str, message: str, *,
        stage: str | None = None,
    ) -> ContractEnvelope:
        payload = {"code": code, "message": message, "retryable": False}
        if stage is not None:
            payload["stage"] = stage
        return self._response(
            request, MessageKind.ERROR,
            payload,
        )

    def _failure(self, request: ContractEnvelope, result) -> ContractEnvelope:
        if result.state is GuardState.SIGNING_DISABLED:
            return self._signing_disabled(request, result.code, result.message)
        if result.state is GuardState.RECOVERY_REQUIRED:
            return self._status(request, MessageKind.RECOVERY_REQUIRED, result.code)
        if result.code == "ACTION_ID_MISMATCH":
            return self.refusal(
                request, RefusalCode.ACTION_ID_INVALID.value,
                "Action identifier does not match.",
            )
        if result.code in REFUSAL_CODES:
            return self.refusal(request, result.code, result.message, stage=result.stage)
        return self.error(request, result.code, result.message, stage=result.stage)

    def _signing_disabled(
        self, request: ContractEnvelope, code: str, message: str = "Wallet authority is disabled."
    ) -> ContractEnvelope:
        return self._response(
            request, MessageKind.SIGNING_DISABLED,
            {"guard_state": GuardState.SIGNING_DISABLED.value, "authority_available": False,
             "code": code, "message": message},
        )

    def _status(self, request: ContractEnvelope, kind: MessageKind, code: str) -> ContractEnvelope:
        requested_id = request.action_id or ""
        operation = self.lifecycle.lending_operation_snapshot.current
        lookup_id = (
            operation.phase_action_id
            if operation is not None and operation.operation_id == requested_id
            else requested_id
        )
        record = self.lifecycle.ledger.find(lookup_id)
        if record is None:
            return self.refusal(request, RefusalCode.ACTION_ID_INVALID.value, "Action was not found.")
        snapshot = self.lifecycle.snapshot
        flow_id = snapshot.flow_id if snapshot.action_id == record.action_id else None
        diagnostic: dict[str, object] = {}
        recovered = False
        try:
            for event in self.audit.journal.events():
                if event.public_fields.get("action_id") != record.action_id:
                    continue
                if event.event_type is EventType.TECHNICAL_ERROR:
                    diagnostic = {
                        "result_stage": event.public_fields.get("stage"),
                        "failure_category": event.public_fields.get("failure_category"),
                        "operation_class": event.public_fields.get("operation_class"),
                        "ipc_outcome": event.public_fields.get("ipc_outcome"),
                    }
                elif event.event_type is EventType.RECOVERY_COMPLETED:
                    recovered = True
        except Exception:
            pass
        payload = {
            "guard_state": snapshot.state.value, "action_state": record.state.value,
            "flow_id": flow_id, "code": code, "message": "Action status is available.",
            "result_code": record.code,
            "result_stage": diagnostic.get("result_stage"),
            "failure_category": diagnostic.get("failure_category"),
            "operation_class": diagnostic.get("operation_class"),
            "ipc_outcome": diagnostic.get("ipc_outcome"),
            "recovery_state": (
                "COMPLETED" if recovered
                else "REQUIRED" if record.state.value == "RECOVERY_REQUIRED"
                else "NOT_REQUIRED"
            ),
        }
        return self._response(request, kind, payload)

    def _health(self, request: ContractEnvelope) -> ContractEnvelope:
        result = self.lifecycle.health()
        failure = getattr(self, "security_failure", None)
        code = failure or (result.code if result.code in SAFE_HEALTH_CODES else "SIGNING_DISABLED")
        return self._response(
            request, MessageKind.HEALTH_RESPONSE,
            {"guard_state": result.state.value, "authority_available": False, "code": code,
             "message": result.message, "compatibility": "COMPATIBLE"},
        )
