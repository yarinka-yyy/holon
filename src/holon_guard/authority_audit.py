from __future__ import annotations

from holon_contracts import ContractEnvelope, MessageKind
from holon_journal import EventType, Journal, UnavailableJournal

from .request_control import RequestController


class AuthorityAudit:
    def __init__(
        self, journal: Journal | UnavailableJournal, requests: RequestController
    ) -> None:
        self.journal = journal
        self.requests = requests

    @staticmethod
    def transfer_fields(
        request: ContractEnvelope, amount_atomic: str | None = None,
        policy_version: str | None = None,
    ) -> dict:
        payload = request.payload
        if request.kind is MessageKind.TRANSFER_INTENT:
            if amount_atomic is None or policy_version is None:
                raise ValueError("Canonical intent audit fields are required")
            return {
                "request_id": request.request_id,
                "action_id": request.action_id,
                "action_type": "transfer",
                "network": payload["network"],
                "recipient": payload["recipient"],
                "asset": payload["asset"],
                "amount_atomic": amount_atomic,
                "policy_version": policy_version,
            }
        return {
            "request_id": request.request_id,
            "action_id": request.action_id,
            "action_type": payload["action_type"],
            "network": payload["network"],
            "recipient": payload["recipient"],
            "asset": payload["asset"],
            "amount_atomic": payload["amount_atomic"],
            "policy_version": payload["policy_version"],
        }

    def transfer(self, event_type: EventType, code: str, request: ContractEnvelope, **extra):
        amount_atomic = extra.pop("canonical_amount_atomic", None)
        policy_version = extra.pop("canonical_policy_version", None)
        fields = self.transfer_fields(request, amount_atomic, policy_version)
        fields.update(extra)
        return self.journal.emit(event_type, code, **fields)

    def event(self, event_type: EventType, code: str, **fields):
        return self.journal.emit(event_type, code, **fields)
