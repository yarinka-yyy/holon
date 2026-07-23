from __future__ import annotations
from datetime import UTC, datetime, timedelta

from holon_contracts import ActionState, RefusalCode, SecurityCode
from holon_guard_ipc import GuardState
from holon_wallet_control import AUTHORITY_VERSION
from .actions import ActionLedgerFailure
from .core import GuardCore
from .model import GuardResult, GuardSnapshot
from .flow_controls import cancel_flow, fail_started_action, interrupt_for_security_block, recover_flow
from .startup import idle_snapshot
class GuardLifecycle(GuardCore):
    def _fail_started_action(self, code: str) -> GuardResult:
        return fail_started_action(self, code)
    def start_flow(self, owner_pid: int, action_id: str, fingerprint: str) -> GuardResult:
        with self._lock:
            try:
                self.ledger.preflight(action_id, fingerprint)
            except ActionLedgerFailure as exc:
                if exc.code == SecurityCode.ACTION_STATE_INVALID.value:
                    return self.disable_signing(exc.code)
                return self._result(False, exc.code, "Action cannot be started.")
            if self.snapshot.state is GuardState.SIGNING_DISABLED:
                return self._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
            if self.snapshot.state is GuardState.RECOVERY_REQUIRED:
                return self._result(False, "RECOVERY_REQUIRED", "Previous flow requires recovery.")
            if self.snapshot.state is not GuardState.NORMAL:
                return self._result(False, RefusalCode.ACTION_ALREADY_ACTIVE.value,
                                    "A protected action is already active.")
            if not self.owner_probe.is_alive(owner_pid):
                return self._result(False, "OWNER_UNAVAILABLE", "Flow owner is unavailable.")
            try:
                self.ledger.begin(action_id, fingerprint)
            except ActionLedgerFailure:
                return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
            flow_id = self.id_factory()
            entering = GuardSnapshot(
                GuardState.ENTERING,
                flow_id,
                owner_pid,
                None,
                "FLOW_STARTING",
                self.clock(),
                action_id,
                fingerprint,
            )
            if not self._persist(entering):
                return self._fail_started_action("STATE_WRITE_FAILED")
            try:
                handle = self.wallet.open_or_activate(flow_id)
                if type(handle.pid) is not int or handle.pid <= 0:
                    raise RuntimeError("Invalid Wallet process")
            except Exception:
                return self._recover("WALLET_LAUNCH_FAILED")
            self.wallet_handle = handle
            active = GuardSnapshot(
                GuardState.ACTIVE,
                flow_id,
                owner_pid,
                handle.pid,
                "FLOW_ACTIVE",
                self.clock(),
                action_id,
                fingerprint,
            )
            if not self._persist(active):
                try:
                    self.wallet.request_close(handle)
                except Exception:
                    pass
                self.wallet_handle = None
                return self._fail_started_action("STATE_WRITE_FAILED")
            try:
                self.ledger.transition(
                    ActionState.AWAITING_LOCAL_CONFIRMATION, "AWAITING_LOCAL_CONFIRMATION"
                )
            except ActionLedgerFailure:
                try:
                    self.wallet.request_close(handle)
                except Exception:
                    pass
                return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
            return self._result(True, "FLOW_STARTED", "Protected flow started.")

    def start_transfer_intent(
        self, owner_pid: int, action_id: str, fingerprint: str,
        intent: dict[str, str], fee_cap_wei: str,
    ) -> tuple[GuardResult, dict[str, object] | None]:
        with self._lock:
            try:
                self.ledger.preflight(action_id, fingerprint)
            except ActionLedgerFailure as exc:
                return self._result(False, exc.code, "Action cannot be started."), None
            if self.snapshot.state is GuardState.SIGNING_DISABLED:
                return self._result(False, "SIGNING_DISABLED", "Wallet authority is disabled."), None
            if self.snapshot.state is GuardState.RECOVERY_REQUIRED:
                return self._result(False, "RECOVERY_REQUIRED", "Previous flow requires recovery."), None
            if self.snapshot.state is not GuardState.NORMAL:
                return self._result(False, RefusalCode.ACTION_ALREADY_ACTIVE.value, "A protected action is already active."), None
            if not self.owner_probe.is_alive(owner_pid):
                return self._result(False, "OWNER_UNAVAILABLE", "Flow owner is unavailable."), None
            try:
                self.ledger.begin(action_id, fingerprint)
            except ActionLedgerFailure:
                return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value), None
            flow_id = self.id_factory()
            created = datetime.fromtimestamp(self.clock(), UTC)
            expires = created + timedelta(minutes=5)
            entering = GuardSnapshot(
                GuardState.ENTERING, flow_id, owner_pid, None, "FLOW_STARTING",
                self.clock(), action_id, fingerprint,
            )
            if not self._persist(entering):
                return self._fail_started_action("STATE_WRITE_FAILED"), None
            request: dict[str, object] = {
                "authority_version": AUTHORITY_VERSION,
                "kind": "prepare_transfer",
                "flow_id": flow_id,
                "action_id": action_id,
                "policy_version": "1",
                **intent,
                "created_at": created.isoformat().replace("+00:00", "Z"),
                "expires_at": expires.isoformat().replace("+00:00", "Z"),
            }
            try:
                prepared = self.wallet.prepare_transfer(request)
            except Exception:
                return self._recover("WALLET_PREPARATION_FAILED"), None
            if not prepared.ok or prepared.payload is None or prepared.handle is None:
                if prepared.code == "WALLET_PREPARATION_AMBIGUOUS":
                    return self._recover(prepared.code), None
                try:
                    self.ledger.terminalize(ActionState.FAILED, prepared.code)
                except ActionLedgerFailure:
                    return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value), None
                self._persist(idle_snapshot(GuardState.NORMAL, prepared.code, self.clock()))
                return self._result(False, prepared.code, "Wallet could not prepare the transfer."), prepared.payload
            payload = prepared.payload
            try:
                fee = int(str(payload["max_total_fee_wei"]))
                if fee <= 0 or fee > int(fee_cap_wei):
                    raise ValueError
                digest = str(payload["prepared_digest"])
            except (KeyError, TypeError, ValueError):
                cancel_request = {
                    "authority_version": AUTHORITY_VERSION,
                    "kind": "cancel_transfer",
                    "flow_id": flow_id,
                    "action_id": action_id,
                    "prepared_digest": str(payload.get("prepared_digest", "")),
                }
                if not self.wallet.cancel_transfer(cancel_request):
                    self.wallet_handle = prepared.handle
                    return self._recover("WALLET_CALLBACK_FAILED"), None
                self.ledger.terminalize(ActionState.FAILED, "MAX_FEE_EXCEEDED")
                self._persist(idle_snapshot(GuardState.NORMAL, "MAX_FEE_EXCEEDED", self.clock()))
                return self._result(False, "MAX_FEE_EXCEEDED", "Maximum fee exceeds policy."), None
            self.wallet_handle = prepared.handle
            active = GuardSnapshot(
                GuardState.ACTIVE, flow_id, owner_pid, prepared.handle.pid,
                "FLOW_ACTIVE", self.clock(), action_id, fingerprint,
            )
            if not self._persist(active):
                try:
                    self.wallet.cancel_transfer({
                        "authority_version": AUTHORITY_VERSION,
                        "kind": "cancel_transfer",
                        "flow_id": flow_id,
                        "action_id": action_id,
                        "prepared_digest": digest,
                    })
                except Exception:
                    pass
                return self._fail_started_action("STATE_WRITE_FAILED"), None
            try:
                self.ledger.transition(ActionState.AWAITING_LOCAL_CONFIRMATION, "AWAITING_LOCAL_CONFIRMATION")
            except ActionLedgerFailure:
                try:
                    self.wallet.cancel_transfer({
                        "authority_version": AUTHORITY_VERSION,
                        "kind": "cancel_transfer",
                        "flow_id": flow_id,
                        "action_id": action_id,
                        "prepared_digest": digest,
                    })
                except Exception:
                    pass
                return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value), None
            self.prepared_digest = digest
            self.authority_expires_at = expires.timestamp()
            return self._result(True, "AWAITING_LOCAL_CONFIRMATION", "Protected flow started."), payload

    def accept_wallet_status(self, update: dict[str, object]) -> bool:
        with self._lock:
            if (
                self.snapshot.state is not GuardState.ACTIVE
                or update.get("flow_id") != self.snapshot.flow_id
                or update.get("action_id") != self.snapshot.action_id
                or update.get("prepared_digest") != self.prepared_digest
                or update.get("wallet_pid") != self.snapshot.wallet_pid
            ):
                return False
            event = update.get("event")
            try:
                if event == "COMPLETED":
                    self.ledger.transition(ActionState.APPROVED, "SUBMISSION_STARTED")
                    self.ledger.terminalize(ActionState.COMPLETED, str(update["code"]))
                elif event == "REJECTED":
                    self.ledger.terminalize(ActionState.REJECTED, str(update["code"]))
                elif event == "FAILED":
                    self.ledger.terminalize(ActionState.FAILED, str(update["code"]))
                else:
                    return False
            except ActionLedgerFailure:
                self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
                return False
            self.wallet_handle = None
            self.prepared_digest = None
            self.authority_expires_at = None
            self._persist(idle_snapshot(GuardState.NORMAL, str(update["code"]), self.clock()))
            return True

    def wallet_status_mismatch(self, code: str) -> None:
        with self._lock:
            if self.snapshot.state in {GuardState.ENTERING, GuardState.ACTIVE}:
                if (
                    self.prepared_digest is not None
                    and self.snapshot.flow_id is not None
                    and self.snapshot.action_id is not None
                ):
                    try:
                        self.wallet.cancel_transfer({
                            "authority_version": AUTHORITY_VERSION,
                            "kind": "cancel_transfer",
                            "flow_id": self.snapshot.flow_id,
                            "action_id": self.snapshot.action_id,
                            "prepared_digest": self.prepared_digest,
                        })
                    except Exception:
                        pass
                self._recover(code)

    def cancel_external_transfer(self, action_id: str) -> GuardResult:
        with self._lock:
            if self.snapshot.state is not GuardState.ACTIVE or self.snapshot.action_id != action_id:
                return self._result(False, "FLOW_NOT_ACTIVE", "No cancellable flow is active.")
            if self.prepared_digest is None or self.snapshot.flow_id is None:
                return self._recover("CALLBACK_STATE_INVALID")
            request = {
                "authority_version": AUTHORITY_VERSION,
                "kind": "cancel_transfer",
                "flow_id": self.snapshot.flow_id,
                "action_id": action_id,
                "prepared_digest": self.prepared_digest,
            }
            if not self.wallet.cancel_transfer(request):
                return self._recover("WALLET_INTERRUPTED")
            try:
                self.ledger.terminalize(ActionState.REJECTED, "ACTION_CANCELLED")
            except ActionLedgerFailure:
                return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
            self.wallet_handle = None
            self.prepared_digest = None
            self.authority_expires_at = None
            self._persist(idle_snapshot(GuardState.NORMAL, "ACTION_CANCELLED", self.clock()))
            return self._result(True, "ACTION_CANCELLED", "Protected flow was cancelled.")
    def cancel_flow(self, action_id: str) -> GuardResult:
        if self.prepared_digest is not None:
            return self.cancel_external_transfer(action_id)
        return cancel_flow(self, action_id)
    def recover_flow(self, action_id: str) -> GuardResult:
        return recover_flow(self, action_id)
    def interrupt_for_security_block(self, code: str) -> GuardResult:
        return interrupt_for_security_block(self, code)
