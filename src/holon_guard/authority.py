"""Contract, policy, journal, request control, and Guard lifecycle boundary."""
from __future__ import annotations

from holon_contracts import ContractEnvelope, MessageKind
from holon_guard_ipc import GuardState
from holon_journal import EventType, JournalFailure
from holon_policy import PolicyEngine

from .authority_audit import AuthorityAudit
from .authority_prepare import prepare
from .authority_responses import REFUSAL_CODES, ResponseMixin
from .lifecycle import GuardLifecycle


class AuthorityService(ResponseMixin):
    refusal_codes = REFUSAL_CODES

    def __init__(
        self, lifecycle: GuardLifecycle, policy: PolicyEngine, audit: AuthorityAudit,
        security_failure: str | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.policy = policy
        self.audit = audit
        self.security_failure = security_failure

    def fail_closed(self, code: str, record_event: bool = True) -> None:
        self.security_failure = code
        if self.lifecycle.snapshot.state in {
            GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING,
        }:
            self.lifecycle.interrupt_for_security_block(code)
        elif self.lifecycle.snapshot.state is not GuardState.RECOVERY_REQUIRED:
            self.lifecycle.disable_signing(code)
        if record_event and not code.startswith("JOURNAL_"):
            try:
                self.audit.event(
                    EventType.SIGNING_DISABLED, code,
                    guard_state=self.lifecycle.snapshot.state.value,
                )
            except JournalFailure as exc:
                self.security_failure = exc.code

    def security_response(self, request: ContractEnvelope):
        code = self.security_failure or "SIGNING_DISABLED"
        if (
            self.lifecycle.snapshot.state is GuardState.RECOVERY_REQUIRED
            and self.lifecycle.ledger.find(request.action_id or "") is not None
        ):
            return self._status(request, MessageKind.RECOVERY_REQUIRED, code)
        return self._signing_disabled(request, code)

    def fail_closed_response(self, request: ContractEnvelope, code: str):
        self.fail_closed(code)
        return self.security_response(request)

    def audit_transfer(self, event_type, code: str, request: ContractEnvelope, **extra) -> bool:
        try:
            self.audit.transfer(event_type, code, request, **extra)
            return True
        except JournalFailure as exc:
            self.fail_closed(exc.code, record_event=False)
            return False

    def audit_system(self, event_type, code: str, **fields) -> bool:
        try:
            self.audit.event(event_type, code, **fields)
            return True
        except JournalFailure as exc:
            self.fail_closed(exc.code, record_event=False)
            return False

    def audit_monitor(self, result, action_id: str | None, flow_id: str | None) -> None:
        if result.code == "ACTION_CANCELLED" and action_id is not None:
            event_type = EventType.ACTION_CANCELLED
        elif result.state is GuardState.RECOVERY_REQUIRED and action_id is not None:
            event_type = EventType.RECOVERY_REQUIRED
        elif result.state is GuardState.SIGNING_DISABLED:
            event_type = EventType.SIGNING_DISABLED
        else:
            return
        fields = {"guard_state": result.state.value}
        if action_id is not None:
            fields["action_id"] = action_id
        if flow_id is not None:
            fields["flow_id"] = flow_id
        self.audit_system(event_type, result.code, **fields)

    def _recover(self, request: ContractEnvelope):
        result = self.lifecycle.recover_flow(request.action_id or "")
        if not result.ok:
            return self._failure(request, result)
        if not self.audit_system(
            EventType.RECOVERY_COMPLETED, result.code, action_id=request.action_id,
            guard_state=result.state.value,
        ):
            return self.security_response(request)
        return self._status(request, MessageKind.ACTION_STATUS, result.code)

    def handle(self, request: ContractEnvelope, owner_pid: int | None) -> ContractEnvelope:
        if request.kind is MessageKind.HEALTH_REQUEST:
            return self._health(request)
        if request.kind is MessageKind.OPEN_WALLET:
            try:
                result = self.lifecycle.wallet.open_public()
            except Exception:
                result = None
            if (
                result is None
                or not result.ok
                or result.wallet_state not in {"OPENED", "ACTIVATED"}
            ):
                return self.error(
                    request,
                    "WALLET_UNAVAILABLE",
                    "Wallet is unavailable.",
                )
            return self._response(
                request,
                MessageKind.WALLET_OPENED,
                {
                    "guard_state": self.lifecycle.snapshot.state.value,
                    "authority_available": False,
                    "wallet_state": result.wallet_state,
                    "code": f"WALLET_{result.wallet_state}",
                    "message": "Wallet is open.",
                },
            )
        if request.kind is MessageKind.READ_WALLET_BALANCES:
            try:
                result = self.lifecycle.wallet.read_public_balances()
            except Exception:
                result = None
            if result is None or not result.ok or result.payload is None:
                return self.error(
                    request,
                    "WALLET_BALANCES_UNAVAILABLE",
                    "Wallet balances are unavailable.",
                )
            return self._response(
                request,
                MessageKind.WALLET_BALANCES,
                result.payload,
            )
        if request.kind is MessageKind.PREPARE_TRANSFER:
            if self.security_failure is not None:
                return self.security_response(request)
            assert owner_pid is not None
            return prepare(self, request, owner_pid)
        if request.kind is MessageKind.ACTION_STATUS_REQUEST:
            return self._status(request, MessageKind.ACTION_STATUS, "ACTION_STATUS")
        if request.kind is MessageKind.CANCEL_ACTION:
            result = self.lifecycle.cancel_flow(request.action_id or "")
            if result.state is GuardState.RECOVERY_REQUIRED and not self.audit_system(
                EventType.RECOVERY_REQUIRED, result.code, action_id=request.action_id,
                guard_state=result.state.value, flow_id=result.flow_id,
            ):
                return self.security_response(request)
            return self._failure(request, result) if not result.ok else self._status(
                request, MessageKind.ACTION_STATUS, result.code
            )
        return self._recover(request)
