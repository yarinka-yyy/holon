from __future__ import annotations
from holon_contracts import ActionState, RefusalCode, SecurityCode
from holon_guard_ipc import GuardState
from .actions import ActionLedgerFailure
from .core import GuardCore
from .model import GuardResult, GuardSnapshot
from .startup import idle_snapshot
ACTIVE_STATES = frozenset({GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING})
class GuardLifecycle(GuardCore):
    def _fail_started_action(self, code: str) -> GuardResult:
        try:
            self.ledger.terminalize(ActionState.FAILED, code)
        except ActionLedgerFailure:
            code = SecurityCode.ACTION_STATE_INVALID.value
        return self.disable_signing(code)
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
    def cancel_flow(self, action_id: str) -> GuardResult:
        with self._lock:
            if self.snapshot.state not in ACTIVE_STATES:
                return self._result(False, "FLOW_NOT_ACTIVE", "No cancellable flow is active.")
            if self.snapshot.action_id != action_id:
                return self._result(False, "ACTION_ID_MISMATCH", "Action identifier does not match.")
            if self.wallet_handle is None:
                return self._recover("WALLET_INTERRUPTED")
            exiting = GuardSnapshot(
                GuardState.EXITING,
                self.snapshot.flow_id,
                self.snapshot.owner_pid,
                self.snapshot.wallet_pid,
                "FLOW_EXITING",
                self.clock(),
                self.snapshot.action_id,
                self.snapshot.action_fingerprint,
            )
            if not self._persist(exiting):
                return self._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
            try:
                self.wallet.request_close(self.wallet_handle)
            except Exception:
                return self._recover("WALLET_INTERRUPTED")
            return self._result(True, "FLOW_EXITING", "Protected flow is closing.")
    def recover_flow(self, action_id: str) -> GuardResult:
        with self._lock:
            if self.snapshot.state is not GuardState.RECOVERY_REQUIRED:
                return self._result(False, "RECOVERY_NOT_REQUIRED", "Recovery is not required.")
            if self.snapshot.action_id != action_id:
                return self._result(False, "ACTION_ID_MISMATCH", "Action identifier does not match.")
            if not self._persist(idle_snapshot(
                GuardState.NORMAL, "RECOVERY_COMPLETED", self.clock()
            )):
                return self._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
            return self._result(True, "RECOVERY_COMPLETED", "Recovery completed; create a new flow.")
