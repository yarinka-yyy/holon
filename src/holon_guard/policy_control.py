"""Guard-owned validation and atomic promotion of Wallet policy drafts."""

from __future__ import annotations

from collections.abc import Mapping

from holon_guard_ipc import GuardState
from holon_contracts import SecurityCode
from holon_policy import (
    PolicyRevisionStale, PolicyRevisionStore,
    PolicyRevisionUnavailable, policy_digest,
)
from holon_wallet.trusted_recipients import (
    TrustedDraftUnavailable, TrustedPolicyDraftStore, trusted_draft_digest,
)
from .provisioning import (
    AUTHORITY_STATE_INITIALIZATION_REQUIRED, AUTHORITY_STATE_READY,
    AuthorityStateProvisioner,
)


class GuardPolicyControl:
    def __init__(
        self, revision_store: PolicyRevisionStore,
        draft_store: TrustedPolicyDraftStore, authority,
        promotion_blocker: str | None = None,
        revision_invalid: bool = False,
        provisioner: AuthorityStateProvisioner | None = None,
        provisioning_blocker: str | None = None,
    ) -> None:
        self.revision_store = revision_store
        self.draft_store = draft_store
        self.authority = authority
        self.promotion_blocker = promotion_blocker
        self.revision_invalid = revision_invalid
        self.provisioner = provisioner
        self.provisioning_blocker = provisioning_blocker

    def handle(self, request: Mapping[str, object]) -> dict[str, object]:
        if request["kind"] == "policy_status":
            return self._status(request)
        if request["kind"] == "apply_draft":
            return self._apply(request)
        if request["kind"] == "initialize_authority_state":
            return self._initialize_authority_state(request)
        if request["kind"] in {
            "resume_lending_operation", "complete_lending_operation",
            "cancel_lending_operation",
        }:
            return self._lending_operation(request)
        return self._set_capability(request)

    def _lending_operation(
        self, request: Mapping[str, object],
    ) -> dict[str, object]:
        lifecycle = self.authority.lifecycle
        operation = lifecycle.lending_operation_snapshot.current
        if operation is None or operation.operation_id != request["operation_id"]:
            return self._refusal(request, "LENDING_OPERATION_NOT_FOUND")
        if request["kind"] == "complete_lending_operation":
            if (
                operation.phase != "resume_or_revoke"
                or operation.phase_action_id != request["phase_action_id"]
                or operation.transaction_hash != request["transaction_hash"]
                or operation.receipt_state not in {"pending", "unknown"}
            ):
                return self._refusal(request, "LENDING_OPERATION_MISMATCH")
            if lifecycle.snapshot.state is GuardState.RECOVERY_REQUIRED:
                recovered = lifecycle.recover_flow(operation.phase_action_id)
                if not recovered.ok:
                    return self._refusal(request, recovered.code)
            elif lifecycle.snapshot.state is not GuardState.NORMAL:
                return self._refusal(request, "POLICY_FLOW_ACTIVE")
            completed = operation.with_receipt(
                str(request["transaction_hash"]), "confirmed", operation.updated_at,
            ).with_phase("completed")
            operation_snapshot_type = type(lifecycle.lending_operation_snapshot)
            if not lifecycle._save_lending_operations(operation_snapshot_type(
                None, lifecycle.lending_operation_snapshot.terminal + (completed,),
            )):
                return self._refusal(request, "LENDING_OPERATION_STATE_INVALID")
            snapshot = self.revision_store.load()
            return self._response(
                request, "lending_operation_completed", "LENDING_OPERATION_COMPLETED",
                snapshot,
            )
        if request["kind"] == "resume_lending_operation":
            if (
                operation.phase != "resume_or_revoke"
                or operation.phase_action_id != request["phase_action_id"]
                or operation.transaction_hash != request["transaction_hash"]
                or operation.receipt_state not in {"pending", "unknown", "confirmed"}
            ):
                return self._refusal(request, "LENDING_OPERATION_MISMATCH")
            if lifecycle.snapshot.state is GuardState.RECOVERY_REQUIRED:
                recovered = lifecycle.recover_flow(operation.phase_action_id)
                if not recovered.ok:
                    return self._refusal(request, recovered.code)
            elif lifecycle.snapshot.state is not GuardState.NORMAL:
                return self._refusal(request, "POLICY_FLOW_ACTIVE")
            try:
                resumed = operation.with_receipt(
                    str(request["transaction_hash"]), "confirmed",
                    operation.updated_at,
                ).resume(int(request["wallet_pid"]))
            except Exception:
                return self._refusal(request, "LENDING_OPERATION_MISMATCH")
            if not lifecycle._save_lending_operations(type(
                lifecycle.lending_operation_snapshot,
            )(resumed, lifecycle.lending_operation_snapshot.terminal)):
                return self._refusal(request, "LENDING_OPERATION_STATE_INVALID")
            snapshot = self.revision_store.load()
            return self._response(
                request, "lending_operation_resumed", "LENDING_OPERATION_RESUMED",
                snapshot,
            )
        if lifecycle.snapshot.state is GuardState.RECOVERY_REQUIRED:
            recovered = lifecycle.recover_flow(operation.phase_action_id)
            if not recovered.ok:
                return self._refusal(request, recovered.code)
        elif lifecycle.snapshot.state is not GuardState.NORMAL:
            return self._refusal(request, "POLICY_FLOW_ACTIVE")
        cancelled = operation.with_phase("cancelled")
        operation_snapshot_type = type(lifecycle.lending_operation_snapshot)
        if not lifecycle._save_lending_operations(operation_snapshot_type(
            None, lifecycle.lending_operation_snapshot.terminal + (cancelled,),
        )):
            return self._refusal(request, "LENDING_OPERATION_STATE_INVALID")
        snapshot = self.revision_store.load()
        return self._response(
            request, "lending_operation_cancelled", "LENDING_OPERATION_CANCELLED",
            snapshot,
        )

    def _initialize_authority_state(
        self, request: Mapping[str, object],
    ) -> dict[str, object]:
        lifecycle = self.authority.lifecycle
        if self.provisioner is None or self.provisioning_blocker is not None:
            return self._refusal(
                request, self.provisioning_blocker or "AUTHORITY_STATE_UNAVAILABLE",
            )
        if (
            lifecycle.snapshot.state is not GuardState.SIGNING_DISABLED
            or lifecycle.ledger.snapshot.current is not None
            or self.provisioner.status() != AUTHORITY_STATE_INITIALIZATION_REQUIRED
        ):
            return self._refusal(request, "AUTHORITY_STATE_NOT_INITIALIZABLE")
        code = self.provisioner.initialize(
            str(request["request_id"]), int(request["expected_policy_revision"]),
            str(request["expected_policy_digest"]), self.authority,
        )
        if code != "AUTHORITY_STATE_INITIALIZED":
            return self._refusal(request, code)
        self.promotion_blocker = None
        snapshot = self.revision_store.load()
        return self._response(
            request, "authority_initialized", code, snapshot,
        )

    def _status(self, request: Mapping[str, object]) -> dict[str, object]:
        try:
            snapshot = self.revision_store.load()
            code = "POLICY_STATUS"
            kind = "policy_status"
        except PolicyRevisionUnavailable:
            snapshot = self.revision_store.recoverable_snapshot()
            code = "POLICY_REVISION_INVALID"
            kind = "policy_refused"
        return self._response(request, kind, code, snapshot)

    def _apply(self, request: Mapping[str, object]) -> dict[str, object]:
        lifecycle = self.authority.lifecycle
        if self.promotion_blocker is not None:
            return self._refusal(request, self.promotion_blocker)
        if (
            lifecycle.snapshot.state not in {GuardState.NORMAL, GuardState.SIGNING_DISABLED}
            or lifecycle.ledger.snapshot.current is not None
        ):
            return self._refusal(request, "POLICY_FLOW_ACTIVE")
        try:
            draft = self.draft_store.load()
            envelope = draft.to_envelope()
            draft_digest = trusted_draft_digest(envelope)
            candidate_digest = policy_digest(envelope["policy"])
            if (
                draft_digest != request["reviewed_draft_digest"]
                or candidate_digest != request["candidate_policy_digest"]
            ):
                return self._refusal(request, "POLICY_DRAFT_CHANGED")
            repair = self.revision_invalid
            try:
                self.revision_store.load()
            except PolicyRevisionUnavailable:
                repair = True
            snapshot, changed = self.revision_store.apply(
                draft.to_policy(),
                draft_digest,
                int(request["expected_policy_revision"]),
                str(request["expected_policy_digest"]),
                repair=repair,
            )
        except TrustedDraftUnavailable:
            return self._refusal(request, "POLICY_DRAFT_UNAVAILABLE")
        except PolicyRevisionStale:
            return self._refusal(request, "POLICY_REVISION_STALE")
        except PolicyRevisionUnavailable:
            return self._refusal(request, "POLICY_REVISION_WRITE_FAILED")
        self.authority.replace_policy_snapshot(snapshot)
        if repair or self.authority.security_failure in {
            SecurityCode.POLICY_STATE_INVALID.value,
            SecurityCode.POLICY_REVISION_CHANGED.value,
        }:
            self.authority.security_failure = None
            self.revision_invalid = False
        lifecycle.enable_signing("AAVE_CAPABILITY_READY")
        code = "POLICY_REVISION_APPLIED" if changed else "POLICY_ALREADY_ACTIVE"
        return self._response(request, "policy_applied", code, snapshot)

    def _set_capability(self, request: Mapping[str, object]) -> dict[str, object]:
        return self._refusal(request, "LENDING_CAPABILITY_BUILT_IN")

    def _refusal(
        self, request: Mapping[str, object], code: str,
    ) -> dict[str, object]:
        try:
            snapshot = self.revision_store.load()
        except PolicyRevisionUnavailable:
            snapshot = self.revision_store.recoverable_snapshot()
        return self._response(request, "policy_refused", code, snapshot)

    def _response(self, request, kind, code, snapshot) -> dict[str, object]:
        return {
            "policy_control_version": "2",
            "kind": kind,
            "request_id": request["request_id"],
            "code": code,
            "policy_revision": snapshot.policy_revision,
            "policy_digest": snapshot.policy_digest,
            "transfer_authority_enabled": snapshot.policy.authority_enabled,
            "lending_authority_enabled": snapshot.policy.lending_authority_enabled,
            "source_draft_digest": snapshot.source_draft_digest,
            "authority_state": (
                self.provisioner.status()
                if self.provisioner is not None else AUTHORITY_STATE_READY
            ),
        }
