from __future__ import annotations

from pathlib import Path

from holon_contracts import make_envelope, MessageKind
from holon_guard.action_store import ActionStateStore
from holon_guard.actions import ActionLedger
from holon_guard.authority_audit import AuthorityAudit
from holon_guard.request_control import RequestController
from holon_guard.request_store import RequestStateStore
from holon_journal import Journal, JournalStore
from holon_policy import Policy, PolicyEngine

ACTION_ID = "act-22222222-2222-4222-8222-222222222222"
ACTION_ID_2 = "act-33333333-3333-4333-8333-333333333333"
FINGERPRINT = "a" * 64
RECIPIENT = "0x1111111111111111111111111111111111111111"


def make_ledger(root: Path, clock=lambda: 2.0) -> ActionLedger:
    store = ActionStateStore(root / "action-state.json")
    return ActionLedger(store, store.bootstrap_empty_for_test(), clock=clock)


def make_audit(root: Path, clock=lambda: 2.0) -> AuthorityAudit:
    journal_store = JournalStore(root / "journal.jsonl")
    journal_store.bootstrap_empty_for_test()
    request_store = RequestStateStore(root / "request-control-state.json")
    requests = RequestController(
        request_store, request_store.bootstrap_empty_for_test(), clock=clock
    )
    return AuthorityAudit(Journal(journal_store), requests)


def enabled_policy() -> PolicyEngine:
    return PolicyEngine(
        Policy.from_dict(
            {
                "schema_version": "1",
                "policy_version": "1",
                "authority_enabled": True,
                "transfer_rules": [
                    {
                        "network": "base",
                        "asset": "usdc",
                        "chain_id": 8453,
                        "max_amount_atomic": "1000000",
                        "max_total_fee_wei": "500",
                    }
                ],
            }
        )
    )


def transfer_request(action_id: str = ACTION_ID, **changes: str):
    payload = {
        "policy_version": "1",
        "action_type": "transfer",
        "network": "base",
        "asset": "usdc",
        "amount_atomic": "1000000",
        "recipient": RECIPIENT,
        "max_total_fee_wei": "500",
    }
    payload.update(changes)
    return make_envelope(MessageKind.PREPARE_TRANSFER, payload, action_id=action_id)
