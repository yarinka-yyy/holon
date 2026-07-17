"""Commands for one active protected flow at a time."""

from __future__ import annotations

from holon_guard_ipc import GuardState

from .core import ACTIVE_STATES, GuardCore
from .model import GuardResult, GuardSnapshot
from .startup import idle_snapshot


class GuardLifecycle(GuardCore):
    def start_flow(self, owner_pid: int) -> GuardResult:
        with self._lock:
            if self.snapshot.state is GuardState.SIGNING_DISABLED:
                return self._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
            if self.snapshot.state is GuardState.RECOVERY_REQUIRED:
                return self._result(False, "RECOVERY_REQUIRED", "Previous flow requires recovery.")
            if self.snapshot.state is not GuardState.NORMAL:
                return self._result(
                    False, "FLOW_ALREADY_ACTIVE", "A protected flow is already active."
                )
            if not self.owner_probe.is_alive(owner_pid):
                return self._result(False, "OWNER_UNAVAILABLE", "Flow owner is unavailable.")
            flow_id = self.id_factory()
            entering = GuardSnapshot(
                GuardState.ENTERING,
                flow_id,
                owner_pid,
                None,
                "FLOW_STARTING",
                self.clock(),
            )
            if not self._persist(entering):
                return self._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
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
            )
            if not self._persist(active):
                try:
                    self.wallet.request_close(handle)
                except Exception:
                    pass
                self.wallet_handle = None
                return self._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
            return self._result(True, "FLOW_STARTED", "Protected flow started.")

    def cancel_flow(self, flow_id: str) -> GuardResult:
        with self._lock:
            if self.snapshot.state not in ACTIVE_STATES:
                return self._result(False, "FLOW_NOT_ACTIVE", "No cancellable flow is active.")
            if self.snapshot.flow_id != flow_id:
                return self._result(False, "FLOW_ID_MISMATCH", "Flow identifier does not match.")
            if self.wallet_handle is None:
                return self._recover("WALLET_INTERRUPTED")
            exiting = GuardSnapshot(
                GuardState.EXITING,
                flow_id,
                self.snapshot.owner_pid,
                self.snapshot.wallet_pid,
                "FLOW_EXITING",
                self.clock(),
            )
            if not self._persist(exiting):
                return self._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
            try:
                self.wallet.request_close(self.wallet_handle)
            except Exception:
                return self._recover("WALLET_INTERRUPTED")
            return self._result(True, "FLOW_EXITING", "Protected flow is closing.")

    def recover_flow(self, flow_id: str) -> GuardResult:
        with self._lock:
            if self.snapshot.state is not GuardState.RECOVERY_REQUIRED:
                return self._result(False, "RECOVERY_NOT_REQUIRED", "Recovery is not required.")
            if self.snapshot.flow_id != flow_id:
                return self._result(False, "FLOW_ID_MISMATCH", "Flow identifier does not match.")
            if not self._persist(
                idle_snapshot(GuardState.NORMAL, "RECOVERY_COMPLETED", self.clock())
            ):
                return self._result(False, "SIGNING_DISABLED", "Wallet authority is disabled.")
            return self._result(
                True, "RECOVERY_COMPLETED", "Recovery completed; create a new flow."
            )
