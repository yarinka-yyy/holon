from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

from holon_contracts import ActionState, RefusalCode, SecurityCode
from holon_guard_ipc import GuardState
from holon_lending import AAVE_MAX_TOTAL_FEE_WEI, AAVE_SAFETY_DIGEST
from holon_wallet_control import AUTHORITY_VERSION
from holon_wallet_control.lending_operation import (
    LendingOperation,
    LendingOperationSnapshot,
)

from .actions import ActionLedgerFailure
from .core import GuardCore
from .model import GuardResult, GuardSnapshot
from .flow_controls import (
    cancel_flow,
    fail_started_action,
    interrupt_for_security_block,
    recover_flow,
)
from .startup import idle_snapshot


class GuardLifecycle(GuardCore):
    def advance_lending_operation(self) -> GuardResult | None:
        """Prepare supply only after Wallet reports a confirmed approve receipt."""
        with self._lock:
            operation = self.lending_operation_snapshot.current
            if operation is None or operation.phase != "prepare_supply":
                return None
            if self.snapshot.state is not GuardState.NORMAL:
                return None
            if not self.owner_probe.is_alive(operation.owner_pid):
                updated = operation.with_phase("resume_or_revoke")
                self._save_lending_operations(LendingOperationSnapshot(
                    updated, self.lending_operation_snapshot.terminal,
                ))
                return self._result(False, "OWNER_UNAVAILABLE", "Supply can be resumed locally.")
            phase_action_id = f"act-{uuid.uuid4()}"
            material = {
                "schema": "lending-phase-1", "operation_id": operation.operation_id,
                "phase_action_id": phase_action_id, "phase": "supply",
                "amount_mode": operation.amount_mode,
                "resolved_amount_atomic": str(operation.resolved_amount_atomic),
                "policy_revision": operation.policy_revision,
                "policy_digest": operation.policy_digest,
                "action_profile_digest": operation.action_profile_digest,
                "safety_digest": operation.safety_digest,
            }
            fingerprint = hashlib.sha256(json.dumps(
                material, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            result, _prepared = self.start_lending_intent(
                operation.owner_pid, phase_action_id, fingerprint,
                {
                    "action": "supply", "amount_mode": operation.amount_mode,
                    "amount": operation.amount,
                }, operation.resolved_amount_atomic, operation.policy_version,
                str(AAVE_MAX_TOTAL_FEE_WEI), None, operation.policy_revision,
                operation.policy_digest, operation.action_profile_digest,
                operation_id=operation.operation_id, phase="supply",
            )
            if not result.ok:
                updated = operation.with_phase("resume_or_revoke")
                self._save_lending_operations(LendingOperationSnapshot(
                    updated, self.lending_operation_snapshot.terminal,
                ))
            return result

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
        intent: dict[str, str], policy_version: str, fee_cap_wei: str,
        policy_revision: int = 0, policy_digest: str = "",
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
                "policy_version": policy_version,
                "policy_revision": policy_revision,
                "policy_digest": policy_digest,
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
                try:
                    self.ledger.terminalize(ActionState.FAILED, "MAX_FEE_EXCEEDED")
                except ActionLedgerFailure:
                    return self.disable_signing(
                        SecurityCode.ACTION_STATE_INVALID.value
                    ), None
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

    def start_lending_intent(
        self, owner_pid: int, action_id: str, fingerprint: str,
        intent: dict[str, object], resolved_amount_atomic: int,
        policy_version: str, fee_cap_wei: str,
        amount_cap_atomic: str | None,
        policy_revision: int, policy_digest: str, action_profile_digest: str,
        operation_id: str | None = None, phase: str = "approve_or_supply",
    ) -> tuple[GuardResult, dict[str, object] | None]:
        """Start one fresh Aave action; never chains another action."""
        with self._lock:
            try:
                self.ledger.preflight(action_id, fingerprint)
            except ActionLedgerFailure as exc:
                return self._result(False, exc.code, "Action cannot be started."), None
            if self.snapshot.state is not GuardState.NORMAL:
                code = "SIGNING_DISABLED" if self.snapshot.state is GuardState.SIGNING_DISABLED else RefusalCode.ACTION_ALREADY_ACTIVE.value
                return self._result(False, code, "Lending authority is unavailable."), None
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
            request = {
                "authority_version": AUTHORITY_VERSION, "kind": "prepare_lending_action",
                "flow_id": flow_id, "action_id": action_id,
                "policy_version": policy_version, "policy_revision": policy_revision,
                "policy_digest": policy_digest,
                "action_profile_digest": action_profile_digest,
                "operation_id": operation_id or action_id,
                "phase_action_id": action_id,
                "phase": phase,
                "resolved_amount_atomic": str(resolved_amount_atomic), **intent,
                "created_at": created.isoformat().replace("+00:00", "Z"),
                "expires_at": expires.isoformat().replace("+00:00", "Z"),
            }
            try:
                prepared = self.wallet.prepare_lending_action(request)
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
                return self._result(False, prepared.code, "Wallet could not prepare the action."), prepared.payload
            payload = prepared.payload
            try:
                fee = int(str(payload["max_total_fee_wei"]))
                amount = int(str(payload["amount_atomic"]))
                digest = str(payload["prepared_digest"])
                prepared_profile_id = str(payload["profile_id"])
                prepared_sender = str(payload["sender"])
                if (
                    fee <= 0 or fee > int(fee_cap_wei)
                    or amount <= 0
                    or not prepared_profile_id or not prepared_sender
                    or amount_cap_atomic is not None and amount > int(amount_cap_atomic)
                ):
                    raise ValueError
            except (KeyError, TypeError, ValueError):
                cancel = {
                    "authority_version": AUTHORITY_VERSION, "kind": "cancel_action",
                    "flow_id": flow_id, "action_id": action_id,
                    "prepared_digest": str(payload.get("prepared_digest", "")),
                }
                if not self.wallet.cancel_transfer(cancel):
                    self.wallet_handle = prepared.handle
                    return self._recover("WALLET_CALLBACK_FAILED"), None
                try:
                    self.ledger.terminalize(ActionState.FAILED, "POLICY_LIMIT_EXCEEDED")
                except ActionLedgerFailure:
                    return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value), None
                self._persist(idle_snapshot(GuardState.NORMAL, "POLICY_LIMIT_EXCEEDED", self.clock()))
                return self._result(False, "POLICY_LIMIT_EXCEEDED", "Lending limits exceed policy."), None
            self.wallet_handle = prepared.handle
            active = GuardSnapshot(
                GuardState.ACTIVE, flow_id, owner_pid, prepared.handle.pid,
                "FLOW_ACTIVE", self.clock(), action_id, fingerprint,
            )
            if not self._persist(active):
                try:
                    self.wallet.cancel_transfer({
                        "authority_version": AUTHORITY_VERSION, "kind": "cancel_action",
                        "flow_id": flow_id, "action_id": action_id,
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
                        "authority_version": AUTHORITY_VERSION, "kind": "cancel_action",
                        "flow_id": flow_id, "action_id": action_id,
                        "prepared_digest": digest,
                    })
                except Exception:
                    pass
                return self.disable_signing(SecurityCode.ACTION_STATE_INVALID.value), None
            self.prepared_digest = digest
            self.authority_expires_at = expires.timestamp()
            if intent.get("action") == "supply":
                current_operation = self.lending_operation_snapshot.current
                operation_phase = (
                    "approve_review" if payload.get("next_action") == "approve"
                    else "supply_review"
                )
                if current_operation is None:
                    operation = LendingOperation(
                        operation_id or action_id, "supply", str(intent["amount_mode"]),
                        intent.get("amount") if isinstance(intent.get("amount"), str) else None,
                        resolved_amount_atomic, owner_pid, policy_version, policy_revision,
                        policy_digest, action_profile_digest, AAVE_SAFETY_DIGEST,
                        operation_phase, action_id, fingerprint,
                        created.isoformat().replace("+00:00", "Z"),
                        prepared_profile_id, prepared_sender,
                    )
                else:
                    if (
                        current_operation.operation_id != (operation_id or action_id)
                        or current_operation.resolved_amount_atomic != resolved_amount_atomic
                    ):
                        return self._recover("LENDING_OPERATION_MISMATCH"), None
                    operation = current_operation.with_phase(
                        operation_phase, phase_action_id=action_id,
                        phase_fingerprint=fingerprint,
                    )
                if not self._save_lending_operations(LendingOperationSnapshot(
                    operation, self.lending_operation_snapshot.terminal,
                )):
                    return self._result(False, "LENDING_OPERATION_STATE_INVALID", "Operation state failed."), None
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
            operation = self.lending_operation_snapshot.current
            is_lending = (
                operation is not None
                and update.get("operation_id") == operation.operation_id
                and update.get("phase_action_id") == operation.phase_action_id
            )
            try:
                if event == "BROADCASTED" and is_lending:
                    self.ledger.transition(ActionState.APPROVED, "SUBMISSION_STARTED")
                    receipt_phase = (
                        "approve_receipt" if update.get("phase") == "approve"
                        else "supply_receipt"
                    )
                    updated = operation.with_phase(receipt_phase).with_receipt(
                        str(update["transaction_hash"]),
                        str(update["receipt_state"]),
                        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    )
                    if not self._save_lending_operations(LendingOperationSnapshot(
                        updated, self.lending_operation_snapshot.terminal,
                    )):
                        return False
                    self.authority_expires_at = None
                    return True
                if event == "RECEIPT_CONFIRMED" and is_lending:
                    operation = operation.with_receipt(
                        str(update["transaction_hash"]), "confirmed",
                        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    )
                    self.ledger.terminalize(ActionState.COMPLETED, str(update["code"]))
                    if update.get("phase") == "approve":
                        updated = operation.with_phase("prepare_supply")
                        next_snapshot = LendingOperationSnapshot(
                            updated, self.lending_operation_snapshot.terminal,
                        )
                    else:
                        completed = operation.with_phase("completed")
                        next_snapshot = LendingOperationSnapshot(
                            None, self.lending_operation_snapshot.terminal + (completed,),
                        )
                    if not self._save_lending_operations(next_snapshot):
                        return False
                elif event == "RECEIPT_FAILED" and is_lending:
                    operation = operation.with_receipt(
                        str(update["transaction_hash"]), "failed",
                        datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                    )
                    self.ledger.terminalize(ActionState.FAILED, str(update["code"]))
                    if update.get("phase") == "supply":
                        updated = operation.with_phase("resume_or_revoke")
                        next_snapshot = LendingOperationSnapshot(
                            updated, self.lending_operation_snapshot.terminal,
                        )
                    else:
                        failed = operation.with_phase("failed")
                        next_snapshot = LendingOperationSnapshot(
                            None, self.lending_operation_snapshot.terminal + (failed,),
                        )
                    if not self._save_lending_operations(next_snapshot):
                        return False
                elif event == "COMPLETED":
                    self.ledger.transition(ActionState.APPROVED, "SUBMISSION_STARTED")
                    self.ledger.terminalize(ActionState.COMPLETED, str(update["code"]))
                elif event == "REJECTED":
                    self.ledger.terminalize(ActionState.REJECTED, str(update["code"]))
                    if is_lending:
                        if operation.phase == "approve_review":
                            cancelled = operation.with_phase("cancelled")
                            next_snapshot = LendingOperationSnapshot(
                                None,
                                self.lending_operation_snapshot.terminal + (cancelled,),
                            )
                        else:
                            next_snapshot = LendingOperationSnapshot(
                                operation.with_phase("resume_or_revoke"),
                                self.lending_operation_snapshot.terminal,
                            )
                        if not self._save_lending_operations(next_snapshot):
                            return False
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
            operation = self.lending_operation_snapshot.current
            if operation is not None and action_id == operation.operation_id:
                action_id = operation.phase_action_id
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
            if operation is not None:
                phase = (
                    "resume_or_revoke"
                    if operation.phase in {"approve_receipt", "prepare_supply", "supply_review", "supply_receipt"}
                    else "cancelled"
                )
                updated = operation.with_phase(phase)
                next_snapshot = (
                    LendingOperationSnapshot(updated, self.lending_operation_snapshot.terminal)
                    if phase == "resume_or_revoke"
                    else LendingOperationSnapshot(
                        None, self.lending_operation_snapshot.terminal + (updated,),
                    )
                )
                self._save_lending_operations(next_snapshot)
            return self._result(True, "ACTION_CANCELLED", "Protected flow was cancelled.")
    def cancel_flow(self, action_id: str) -> GuardResult:
        if self.prepared_digest is not None:
            return self.cancel_external_transfer(action_id)
        return cancel_flow(self, action_id)
    def recover_flow(self, action_id: str) -> GuardResult:
        return recover_flow(self, action_id)
    def interrupt_for_security_block(self, code: str) -> GuardResult:
        return interrupt_for_security_block(self, code)
