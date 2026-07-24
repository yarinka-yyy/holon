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


class GuardPolicyControl:
    def __init__(
        self, revision_store: PolicyRevisionStore,
        draft_store: TrustedPolicyDraftStore, authority,
        promotion_blocker: str | None = None,
        revision_invalid: bool = False,
    ) -> None:
        self.revision_store = revision_store
        self.draft_store = draft_store
        self.authority = authority
        self.promotion_blocker = promotion_blocker
        self.revision_invalid = revision_invalid

    def handle(self, request: Mapping[str, object]) -> dict[str, object]:
        if request["kind"] == "policy_status":
            return self._status(request)
        return self._apply(request)

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
        lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        code = "POLICY_REVISION_APPLIED" if changed else "POLICY_ALREADY_ACTIVE"
        return self._response(request, "policy_applied", code, snapshot)

    def _refusal(
        self, request: Mapping[str, object], code: str,
    ) -> dict[str, object]:
        try:
            snapshot = self.revision_store.load()
        except PolicyRevisionUnavailable:
            snapshot = self.revision_store.recoverable_snapshot()
        return self._response(request, "policy_refused", code, snapshot)

    @staticmethod
    def _response(request, kind, code, snapshot) -> dict[str, object]:
        return {
            "policy_control_version": "1",
            "kind": kind,
            "request_id": request["request_id"],
            "code": code,
            "policy_revision": snapshot.policy_revision,
            "policy_digest": snapshot.policy_digest,
        }
