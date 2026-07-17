from __future__ import annotations

import threading
import time
import uuid
from typing import Callable

from holon_contracts import ActionState, SecurityCode
from holon_guard_ipc import GuardState

from .actions import ActionLedger, ActionLedgerFailure
from .model import GuardResult, GuardSnapshot
from .reconcile import reconcile_action_state
from .startup import best_effort_save, idle_snapshot, restore_snapshot
from .store import SnapshotStore
from .wallet import OwnerProbe, WalletController, WalletHandle
class GuardCore:
    def __init__(
        self,
        store: SnapshotStore,
        snapshot: GuardSnapshot,
        wallet: WalletController,
        owner_probe: OwnerProbe,
        ledger: ActionLedger,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.snapshot = snapshot
        self.wallet = wallet
        self.owner_probe = owner_probe
        self.ledger = ledger
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self.clock = clock
        self.wallet_handle: WalletHandle | None = None
        self._lock = threading.RLock()

    @classmethod
    def restore(
        cls, store: SnapshotStore, wallet: WalletController, owner_probe: OwnerProbe,
        ledger: ActionLedger,
    ) -> "GuardCore":
        snapshot = restore_snapshot(store)
        guard = cls(store, snapshot, wallet, owner_probe, ledger)
        reconcile_action_state(guard)
        return guard

    def _result(self, ok: bool, code: str, message: str) -> GuardResult:
        return GuardResult(ok, code, self.snapshot.state, message, self.snapshot.flow_id)

    def _persist(self, snapshot: GuardSnapshot) -> bool:
        try:
            self.store.save(snapshot)
            self.snapshot = snapshot
            return True
        except OSError:
            self.snapshot = idle_snapshot(
                GuardState.SIGNING_DISABLED, "STATE_WRITE_FAILED", self.clock()
            )
            best_effort_save(self.store, self.snapshot)
            return False

    def _recover(self, code: str) -> GuardResult:
        try:
            self.ledger.terminalize(ActionState.RECOVERY_REQUIRED, code)
        except ActionLedgerFailure:
            return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
        recovery = GuardSnapshot(
            GuardState.RECOVERY_REQUIRED,
            self.snapshot.flow_id,
            None,
            None,
            code,
            self.clock(),
            self.snapshot.action_id,
            self.snapshot.action_fingerprint,
        )
        self.wallet_handle = None
        self._persist(recovery)
        return self._result(False, code, "Protected flow requires recovery.")

    def health(self) -> GuardResult:
        with self._lock:
            code = (
                self.snapshot.reason
                if self.snapshot.state is GuardState.SIGNING_DISABLED
                else "OK"
            )
            return self._result(True, code, "Guard health is available.")

    def monitor_once(self) -> GuardResult:
        with self._lock:
            if self.snapshot.state not in {GuardState.ACTIVE, GuardState.EXITING}:
                return self.health()
            if self.snapshot.owner_pid is None or not self.owner_probe.is_alive(
                self.snapshot.owner_pid
            ):
                return self._recover("OWNER_INTERRUPTED")
            if self.wallet_handle is None:
                return self._recover("WALLET_INTERRUPTED")
            exit_code = self.wallet_handle.poll()
            if exit_code is None:
                return self.health()
            if self.snapshot.state is GuardState.ACTIVE or exit_code != 0:
                return self._recover("WALLET_INTERRUPTED")
            try:
                self.ledger.terminalize(ActionState.REJECTED, "ACTION_CANCELLED")
            except ActionLedgerFailure:
                return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
            self.wallet_handle = None
            self._persist(idle_snapshot(GuardState.NORMAL, "ACTION_CANCELLED", self.clock()))
            return self._result(True, "ACTION_CANCELLED", "Protected flow was cancelled.")

    def disable_signing(self, reason: str = "SIGNING_DISABLED") -> GuardResult:
        with self._lock:
            self.wallet_handle = None
            self._persist(
                idle_snapshot(GuardState.SIGNING_DISABLED, reason, self.clock())
            )
            return self._result(True, "SIGNING_DISABLED", "Wallet authority is disabled.")
