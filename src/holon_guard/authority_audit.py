from __future__ import annotations

from holon_contracts import ContractEnvelope
from holon_journal import EventType, Journal, UnavailableJournal

from .request_control import RequestController


class AuthorityAudit:
    def __init__(
        self, journal: Journal | UnavailableJournal, requests: RequestController
    ) -> None:
        self.journal = journal
        self.requests = requests

    @staticmethod
    def transfer_fields(request: ContractEnvelope) -> dict:
        payload = request.payload
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
        fields = self.transfer_fields(request)
        fields.update(extra)
        return self.journal.emit(event_type, code, **fields)

    def event(self, event_type: EventType, code: str, **fields):
        return self.journal.emit(event_type, code, **fields)
