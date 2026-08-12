from __future__ import annotations

import threading
import time
import uuid
from typing import Callable

from holon_contracts import ActionState, SecurityCode
from holon_guard_ipc import GuardState
from holon_wallet_control import AUTHORITY_VERSION
from holon_wallet_control.lending_operation import (
    LendingOperationSnapshot, LendingOperationStateError, LendingOperationStore,
)

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
        lending_operation_store: LendingOperationStore | None = None,
        lending_operation_snapshot: LendingOperationSnapshot | None = None,
        id_factory: Callable[[], str] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.snapshot = snapshot
        self.wallet = wallet
        self.owner_probe = owner_probe
        self.ledger = ledger
        self.lending_operation_store = lending_operation_store
        self.lending_operation_snapshot = (
            lending_operation_snapshot or LendingOperationSnapshot()
        )
        self.id_factory = id_factory or (lambda: str(uuid.uuid4()))
        self.clock = clock
        self.wallet_handle: WalletHandle | None = None
        self.prepared_digest: str | None = None
        self.prepared_audit_context: dict[str, object] | None = None
        self.authority_expires_at: float | None = None
        self._lock = threading.RLock()

    @classmethod
    def restore(
        cls, store: SnapshotStore, wallet: WalletController, owner_probe: OwnerProbe,
        ledger: ActionLedger,
        lending_operation_store: LendingOperationStore | None = None,
        lending_operation_snapshot: LendingOperationSnapshot | None = None,
    ) -> "GuardCore":
        snapshot = restore_snapshot(store)
        guard = cls(
            store, snapshot, wallet, owner_probe, ledger,
            lending_operation_store, lending_operation_snapshot,
        )
        reconcile_action_state(guard)
        operation = guard.lending_operation_snapshot.current
        if (
            guard.snapshot.state is GuardState.NORMAL
            and guard.ledger.snapshot.current is None
            and operation is not None
            and operation.phase != "resume_or_revoke"
        ):
            if operation.phase == "approve_review":
                cancelled = operation.with_phase("cancelled")
                guard._save_lending_operations(LendingOperationSnapshot(
                    None, guard.lending_operation_snapshot.terminal + (cancelled,),
                ))
            elif operation.phase in {
                "approve_receipt", "prepare_supply", "supply_review",
                "supply_receipt",
            }:
                guard._save_lending_operations(LendingOperationSnapshot(
                    operation.with_phase("resume_or_revoke"),
                    guard.lending_operation_snapshot.terminal,
                ))
        return guard

    def _save_lending_operations(self, snapshot: LendingOperationSnapshot) -> bool:
        if self.lending_operation_store is None:
            self.lending_operation_snapshot = snapshot
            return True
        try:
            self.lending_operation_store.save(snapshot)
        except (OSError, LendingOperationStateError, TypeError, ValueError):
            self.disable_signing("LENDING_OPERATION_STATE_INVALID")
            return False
        self.lending_operation_snapshot = snapshot
        return True

    def _result(
        self, ok: bool, code: str, message: str, *, stage: str | None = None,
    ) -> GuardResult:
        return GuardResult(
            ok, code, self.snapshot.state, message, self.snapshot.flow_id, stage,
        )

    def _set_prepared_audit_context(
        self, payload: dict[str, object], action_type: str,
    ) -> None:
        selector = payload.get("selector")
        self.prepared_audit_context = {
            "action_type": action_type,
            "network": str(payload["network"]),
            "wallet_address": str(payload["sender"]),
            "recipient": str(payload.get("recipient") or payload["target"]),
            "asset": str(payload["asset"]),
            "amount_atomic": str(payload["amount_atomic"]),
            "contract": str(payload["target"]) if selector is not None else None,
            "selector": selector,
            "calldata_hash": str(payload["calldata_hash"]),
            "local_approved_recorded": False,
            "contract_action_recorded": False,
        }

    def _force_prepared_recovery(
        self, previous: GuardSnapshot, code: str,
    ) -> GuardResult:
        try:
            self.ledger.terminalize(ActionState.RECOVERY_REQUIRED, code)
        except ActionLedgerFailure:
            code = SecurityCode.ACTION_STATE_INVALID.value
        recovery = GuardSnapshot(
            GuardState.RECOVERY_REQUIRED,
            previous.flow_id,
            None,
            None,
            code,
            self.clock(),
            previous.action_id,
            previous.action_fingerprint,
        )
        self.wallet_handle = None
        self.prepared_digest = None
        self.prepared_audit_context = None
        self.authority_expires_at = None
        self.snapshot = recovery
        best_effort_save(self.store, recovery)
        return self._result(False, code, "Prepared Wallet action requires recovery.")

    def _contain_prepared_failure(
        self, previous: GuardSnapshot, digest: str, handle: WalletHandle,
        code: str, *, lending: bool,
    ) -> GuardResult:
        cancelled = False
        try:
            cancelled = self.wallet.cancel_transfer({
                "authority_version": AUTHORITY_VERSION,
                "kind": "cancel_action" if lending else "cancel_transfer",
                "flow_id": previous.flow_id,
                "action_id": previous.action_id,
                "prepared_digest": digest,
            })
        except Exception:
            cancelled = False
        if cancelled:
            try:
                self.ledger.terminalize(ActionState.FAILED, code)
            except ActionLedgerFailure:
                code = SecurityCode.ACTION_STATE_INVALID.value
            self.disable_signing(code)
            return self._result(False, code, "Prepared Wallet action was cancelled.")
        try:
            self.wallet.request_close(handle)
        except Exception:
            pass
        return self._force_prepared_recovery(previous, code)

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

    def _recover(self, code: str, *, stage: str | None = None) -> GuardResult:
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
        self.prepared_digest = None
        self.prepared_audit_context = None
        self.authority_expires_at = None
        operation = self.lending_operation_snapshot.current
        if operation is not None:
            if operation.phase == "approve_review":
                cancelled = operation.with_phase("cancelled")
                saved = self._save_lending_operations(LendingOperationSnapshot(
                    None, self.lending_operation_snapshot.terminal + (cancelled,),
                ))
            elif operation.phase in {
                "approve_receipt", "prepare_supply", "supply_review", "supply_receipt",
            }:
                saved = self._save_lending_operations(LendingOperationSnapshot(
                    operation.with_phase("resume_or_revoke"),
                    self.lending_operation_snapshot.terminal,
                ))
            else:
                saved = True
            if not saved:
                return self.disable_signing("LENDING_OPERATION_STATE_INVALID")
        self._persist(recovery)
        return self._result(False, code, "Protected flow requires recovery.", stage=stage)

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
            if (
                self.authority_expires_at is not None
                and self.clock() >= self.authority_expires_at
            ):
                if (
                    self.snapshot.flow_id is None
                    or self.snapshot.action_id is None
                    or self.prepared_digest is None
                    or not self.wallet.cancel_transfer({
                        "authority_version": AUTHORITY_VERSION,
                        "kind": "cancel_transfer",
                        "flow_id": self.snapshot.flow_id,
                        "action_id": self.snapshot.action_id,
                        "prepared_digest": self.prepared_digest,
                    })
                ):
                    return self._recover("WALLET_CALLBACK_FAILED")
                try:
                    self.ledger.terminalize(ActionState.FAILED, "ACTION_EXPIRED")
                except ActionLedgerFailure:
                    return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value)
                self.wallet_handle = None
                self.prepared_digest = None
                self.authority_expires_at = None
                self._persist(idle_snapshot(GuardState.NORMAL, "ACTION_EXPIRED", self.clock()))
                return self._result(False, "ACTION_EXPIRED", "Protected flow expired.")
            if self.snapshot.owner_pid is None or not self.owner_probe.is_alive(
                self.snapshot.owner_pid
            ):
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
            self.prepared_digest = None
            self.prepared_audit_context = None
            self.authority_expires_at = None
            self._persist(
                idle_snapshot(GuardState.SIGNING_DISABLED, reason, self.clock())
            )
            return self._result(True, "SIGNING_DISABLED", "Wallet authority is disabled.")

    def enable_signing(self, reason: str = "POLICY_AUTHORITY_ENABLED") -> GuardResult:
        with self._lock:
            if self.snapshot.state not in {GuardState.NORMAL, GuardState.SIGNING_DISABLED}:
                return self._result(False, "POLICY_FLOW_ACTIVE", "A protected flow is active.")
            if self.ledger.snapshot.current is not None:
                return self._result(False, "POLICY_FLOW_ACTIVE", "An authority action is active.")
            self._persist(idle_snapshot(GuardState.NORMAL, reason, self.clock()))
            return self._result(True, reason, "Wallet authority is available.")
