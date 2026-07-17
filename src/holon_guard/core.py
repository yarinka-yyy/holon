"""Guard startup, persistence failure handling, and process monitoring."""

from __future__ import annotations

import threading
import time
import uuid
from typing import Callable

from holon_guard_ipc import GuardState

from .model import GuardResult, GuardSnapshot
from .startup import best_effort_save, idle_snapshot, restore_snapshot
from .store import SnapshotStore
from .wallet import OwnerProbe, WalletController, WalletHandle

ACTIVE_STATES = frozenset({GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING})


class GuardCore:
    def __init__(
        self,
        store: SnapshotStore,
        snapshot: GuardSnapshot,
        wallet: WalletController,
        owner_probe: OwnerProbe,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.snapshot = snapshot
        self.wallet = wallet
        self.owner_probe = owner_probe
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self.clock = clock
        self.wallet_handle: WalletHandle | None = None
        self._lock = threading.RLock()

    @classmethod
    def restore(
        cls, store: SnapshotStore, wallet: WalletController, owner_probe: OwnerProbe
    ) -> "GuardCore":
        snapshot = restore_snapshot(store)
        return cls(store, snapshot, wallet, owner_probe)

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
        recovery = GuardSnapshot(
            GuardState.RECOVERY_REQUIRED,
            self.snapshot.flow_id,
            None,
            None,
            code,
            self.clock(),
        )
        self.wallet_handle = None
        self._persist(recovery)
        return self._result(False, code, "Protected flow requires recovery.")

    def health(self) -> GuardResult:
        with self._lock:
            code = (
                "SIGNING_DISABLED"
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
            if exit_code != 0:
                return self._recover("WALLET_INTERRUPTED")
            self.wallet_handle = None
            self._persist(idle_snapshot(GuardState.NORMAL, "WALLET_CLOSED", self.clock()))
            return self._result(True, "FLOW_COMPLETED", "Protected flow ended.")

    def disable_signing(self, reason: str = "SIGNING_DISABLED") -> GuardResult:
        with self._lock:
            self.wallet_handle = None
            self._persist(
                idle_snapshot(GuardState.SIGNING_DISABLED, reason, self.clock())
            )
            return self._result(True, "SIGNING_DISABLED", "Wallet authority is disabled.")
