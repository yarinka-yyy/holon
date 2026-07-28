from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from holon_contracts import MessageKind, SecurityCode, make_envelope
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.actions import ActionLedgerFailure
from holon_guard.authority import AuthorityService
from holon_guard.wallet import WalletPreparedResult
from holon_journal import EventType, JournalFailure
from holon_policy import Policy, PolicyEngine, PolicySnapshot
from guard_support import ACTION_ID, RECIPIENT, enabled_policy, make_audit, make_ledger


class Handle:
    pid = 404

    def poll(self):
        return None


class Owner:
    def is_alive(self, pid):
        return pid > 0


class AuthorityWallet:
    def __init__(self, fee="400", refusal=None):
        self.handle = Handle()
        self.fee = fee
        self.refusal = refusal
        self.prepares = []
        self.cancels = []

    def prepare_transfer(self, request):
        self.prepares.append(request)
        if self.refusal:
            return WalletPreparedResult(False, self.refusal, None, self.handle)
        native = request["asset"] == "eth"
        return WalletPreparedResult(True, "TRANSFER_PREPARED", {
            "authority_version": request["authority_version"], "kind": "transfer_prepared",
            "flow_id": request["flow_id"], "action_id": request["action_id"],
            "wallet_pid": self.handle.pid, "profile_id": "profile-one",
            "sender": "0x2222222222222222222222222222222222222222",
            "recipient": request["recipient"], "network": request["network"],
            "asset": request["asset"], "amount_atomic": request["amount_atomic"],
            "target": (
                request["recipient"] if native
                else "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
            ),
            "selector": None if native else "0xa9059cbb",
            "calldata_hash": "c" * 64,
            "policy_revision": request["policy_revision"],
            "policy_digest": request["policy_digest"],
            "max_total_fee_wei": self.fee, "prepared_digest": "a" * 64,
            "created_at": request["created_at"], "expires_at": request["expires_at"],
            "code": "TRANSFER_PREPARED",
        }, self.handle)

    def cancel_transfer(self, request):
        self.cancels.append(request)
        return True

    def open_or_activate(self, flow_id):
        del flow_id
        return self.handle

    def request_close(self, handle):
        del handle


def intent(action_id=ACTION_ID, **changes):
    payload = {
        "network": "base", "asset": "usdc", "amount": "1",
        "recipient": RECIPIENT,
    }
    payload.update(changes)
    return make_envelope(MessageKind.TRANSFER_INTENT, payload, action_id=action_id)


def service(root: Path, wallet: AuthorityWallet, policy=None):
    store = SnapshotStore(root / "guard-state.json")
    store.bootstrap_normal_for_test(1.0)
    lifecycle = GuardLifecycle(
        store, store.load(), wallet, Owner(), make_ledger(root),
    )
    authority = AuthorityService(
        lifecycle, policy or enabled_policy(), make_audit(root),
    )
    return lifecycle, authority


def test_exact_intent_waits_for_wallet_preflight_and_completes_status():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        response = authority.handle(intent(), owner_pid=123)
        assert response.kind is MessageKind.PROTECTED_FLOW_STARTED
        assert response.payload["action_state"] == "AWAITING_LOCAL_CONFIRMATION"
        assert lifecycle.snapshot.state.value == "ACTIVE"
        request = wallet.prepares[0]
        assert request["amount_atomic"] == "1000000"
        assert request["authority_version"] == "2"
        assert request["policy_revision"] == 0
        assert len(request["policy_digest"]) == 64
        assert "max_total_fee_wei" not in intent().payload
        assert authority.accept_wallet_status({
            "flow_id": lifecycle.snapshot.flow_id,
            "action_id": ACTION_ID,
            "prepared_digest": "a" * 64,
            "wallet_pid": 404,
            "event": "COMPLETED",
            "code": "PENDING",
            "outcome": "pending",
        })
        assert lifecycle.snapshot.state.value == "NORMAL"
        assert lifecycle.ledger.find(ACTION_ID).state.value == "COMPLETED"
        events = authority.audit.journal.events()
        lifecycle_events = [event for event in events if event.event_type in {
            EventType.LOCAL_APPROVED, EventType.CONTRACT_ACTION,
            EventType.BROADCAST_RESULT,
        }]
        assert [event.event_type for event in lifecycle_events] == [
            EventType.LOCAL_APPROVED, EventType.CONTRACT_ACTION,
            EventType.BROADCAST_RESULT,
        ]
        for event in lifecycle_events:
            assert event.public_fields["wallet_address"] == (
                "0x2222222222222222222222222222222222222222"
            )
            assert event.public_fields["recipient"] == RECIPIENT
            assert event.public_fields["asset"] == "usdc"
            assert event.public_fields["amount_atomic"] == "1000000"
        contract = lifecycle_events[1].public_fields
        assert contract["contract"] == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
        assert contract["selector"] == "0xa9059cbb"
        assert contract["calldata_hash"] == "c" * 64
        assert "password" not in str([event.to_dict() for event in events]).lower()


def test_local_rejection_writes_only_rejection_outcome():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        authority.handle(intent(), owner_pid=123)
        assert authority.accept_wallet_status({
            "flow_id": lifecycle.snapshot.flow_id,
            "action_id": ACTION_ID,
            "prepared_digest": "a" * 64,
            "wallet_pid": 404,
            "event": "REJECTED", "code": "LOCAL_CANCELLED",
        })
        types = [event.event_type for event in authority.audit.journal.events()]
        assert types.count(EventType.LOCAL_REJECTED) == 1
        assert EventType.LOCAL_APPROVED not in types
        assert EventType.CONTRACT_ACTION not in types
        assert EventType.BROADCAST_RESULT not in types


def test_wallet_outcome_journal_failure_disables_signing():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        authority.handle(intent(), owner_pid=123)
        update = {
            "flow_id": lifecycle.snapshot.flow_id,
            "action_id": ACTION_ID,
            "prepared_digest": "a" * 64,
            "wallet_pid": 404,
            "event": "COMPLETED", "code": "PENDING",
            "outcome": "pending",
        }
        with patch.object(
            authority.audit.journal, "emit",
            side_effect=JournalFailure("JOURNAL_WRITE_FAILED"),
        ):
            assert not authority.accept_wallet_status(update)
        assert lifecycle.snapshot.state.value == "SIGNING_DISABLED"
        assert authority.security_failure == "JOURNAL_WRITE_FAILED"


def test_native_eth_completion_has_no_contract_action():
    policy = PolicyEngine(Policy.from_dict({
        "schema_version": "2", "policy_version": "1",
        "authority_enabled": True,
        "transfer_rules": [{
            "network": "base", "asset": "eth", "chain_id": 8453,
            "max_amount_atomic": "1000000000000000",
            "max_total_fee_wei": "500",
            "recipients": [{
                "address": RECIPIENT,
                "max_amount_atomic": "1000000000000000",
            }],
        }],
    }))
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet, policy)
        response = authority.handle(
            intent(asset="eth", amount="0.001"), owner_pid=123,
        )
        assert response.kind is MessageKind.PROTECTED_FLOW_STARTED
        assert authority.accept_wallet_status({
            "flow_id": lifecycle.snapshot.flow_id,
            "action_id": ACTION_ID,
            "prepared_digest": "a" * 64,
            "wallet_pid": 404,
            "event": "COMPLETED", "code": "PENDING",
            "outcome": "pending",
        })
        types = [event.event_type for event in authority.audit.journal.events()]
        assert EventType.LOCAL_APPROVED in types
        assert EventType.BROADCAST_RESULT in types
        assert EventType.CONTRACT_ACTION not in types


def test_prebroadcast_wallet_failure_records_technical_error_only():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        authority.handle(intent(), owner_pid=123)
        assert authority.accept_wallet_status({
            "flow_id": lifecycle.snapshot.flow_id,
            "action_id": ACTION_ID,
            "prepared_digest": "a" * 64,
            "wallet_pid": 404,
            "event": "FAILED", "code": "WALLET_PREPARE_FAILED",
        })
        types = [event.event_type for event in authority.audit.journal.events()]
        assert EventType.TECHNICAL_ERROR in types
        assert EventType.LOCAL_APPROVED not in types
        assert EventType.BROADCAST_RESULT not in types


def test_guard_revision_change_interrupts_active_flow_without_retry():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        assert authority.handle(intent(), owner_pid=123).kind is MessageKind.PROTECTED_FLOW_STARTED
        current = authority.policy_snapshot

        class ChangedStore:
            def load(self):
                return PolicySnapshot(
                    current.policy_revision + 1, "f" * 64, current.policy,
                )

        authority.revision_store = ChangedStore()
        assert not authority.revalidate_policy()
        assert lifecycle.snapshot.state.value == "RECOVERY_REQUIRED"
        assert lifecycle.snapshot.reason == "POLICY_REVISION_CHANGED"
        assert len(wallet.cancels) == 1
        assert lifecycle.ledger.find(ACTION_ID).state.value == "RECOVERY_REQUIRED"


def test_disabled_policy_and_amount_cap_refuse_before_wallet():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        disabled = PolicyEngine(Policy("2", "1", False, ()))
        lifecycle, authority = service(Path(temporary), wallet, disabled)
        result = authority.handle(intent(), owner_pid=123)
        assert result.payload["code"] == "POLICY_AUTHORITY_DISABLED"
        assert wallet.prepares == []
        assert lifecycle.snapshot.state.value == "NORMAL"


def test_unknown_recipient_is_refused_before_wallet_or_protected_flow():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        result = authority.handle(
            intent(recipient="0x3333333333333333333333333333333333333333"),
            owner_pid=123,
        )
        assert result.kind is MessageKind.REFUSAL
        assert result.payload["code"] == "RECIPIENT_NOT_ALLOWED"
        assert wallet.prepares == []
        assert lifecycle.snapshot.state.value == "NORMAL"


def test_recipient_amount_cap_is_refused_before_wallet_or_protected_flow():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        result = authority.handle(intent(amount="1.000001"), owner_pid=123)
        assert result.kind is MessageKind.REFUSAL
        assert result.payload["code"] == "AMOUNT_LIMIT_EXCEEDED"
        assert wallet.prepares == []
        assert lifecycle.snapshot.state.value == "NORMAL"


def test_signing_disabled_guard_refuses_before_wallet_launch():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        result = authority.handle(intent(), owner_pid=123)
        assert result.kind is MessageKind.SIGNING_DISABLED
        assert result.payload["code"] == "POLICY_AUTHORITY_DISABLED"
        assert wallet.prepares == []


def test_prepared_fee_above_guard_cap_cancels_wallet_without_active_authority():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet(fee="501")
        lifecycle, authority = service(Path(temporary), wallet)
        result = authority.handle(intent(), owner_pid=123)
        assert result.payload["code"] == "MAX_FEE_EXCEEDED"
        assert len(wallet.prepares) == 1
        assert len(wallet.cancels) == 1
        assert lifecycle.snapshot.state.value == "NORMAL"
        assert lifecycle.ledger.find(ACTION_ID).state.value == "FAILED"


def test_fee_rejection_action_state_failure_disables_signing(monkeypatch):
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet(fee="501")
        lifecycle, authority = service(Path(temporary), wallet)

        def fail_terminalize(*_args):
            raise ActionLedgerFailure(SecurityCode.ACTION_STATE_INVALID.value)

        monkeypatch.setattr(lifecycle.ledger, "terminalize", fail_terminalize)
        result = authority.handle(intent(), owner_pid=123)

        assert result.kind is MessageKind.ERROR
        assert len(wallet.prepares) == 1
        assert len(wallet.cancels) == 1
        assert lifecycle.snapshot.state.value == "SIGNING_DISABLED"
        assert lifecycle.snapshot.reason == SecurityCode.ACTION_STATE_INVALID.value
        assert lifecycle.wallet_handle is None
        assert lifecycle.prepared_digest is None
        assert lifecycle.monitor_once().state.value == "SIGNING_DISABLED"

        retry = authority.handle(
            intent(action_id="act-33333333-3333-4333-8333-333333333333"),
            owner_pid=123,
        )
        assert retry.kind is MessageKind.SIGNING_DISABLED
        assert len(wallet.prepares) == 1
        assert len(wallet.cancels) == 1


def test_hermes_cancel_keeps_wallet_process_and_rejects_action():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        authority.handle(intent(), owner_pid=123)
        cancel = make_envelope(MessageKind.CANCEL_ACTION, {}, action_id=ACTION_ID)
        result = authority.handle(cancel, owner_pid=None)
        assert result.payload["action_state"] == "REJECTED"
        assert lifecycle.snapshot.state.value == "NORMAL"
        assert len(wallet.cancels) == 1


def test_intent_replay_and_mutation_are_terminally_refused():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        _lifecycle, authority = service(Path(temporary), wallet)
        authority.handle(intent(), owner_pid=123)
        replay = authority.handle(intent(), owner_pid=123)
        mutated = authority.handle(intent(amount="0.5"), owner_pid=123)
        assert replay.payload["code"] == "ACTION_REPLAYED"
        assert mutated.payload["code"] == "ACTION_MUTATED"


def test_expiry_invalidates_wallet_action_without_leaving_guard_active():
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        clock = [1_800_000_000.0]
        wallet = AuthorityWallet()
        store = SnapshotStore(root / "guard-state.json")
        store.bootstrap_normal_for_test(clock[0])
        lifecycle = GuardLifecycle(
            store, store.load(), wallet, Owner(), make_ledger(root),
            clock=lambda: clock[0],
        )
        authority = AuthorityService(lifecycle, enabled_policy(), make_audit(root))
        authority.handle(intent(), owner_pid=123)
        clock[0] += 301
        result = lifecycle.monitor_once()
        assert result.code == "ACTION_EXPIRED"
        assert lifecycle.snapshot.state.value == "NORMAL"
        assert lifecycle.ledger.find(ACTION_ID).state.value == "FAILED"
        assert len(wallet.cancels) == 1


def test_status_digest_mismatch_enters_recovery():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        lifecycle, authority = service(Path(temporary), wallet)
        authority.handle(intent(), owner_pid=123)
        assert not lifecycle.accept_wallet_status({
            "flow_id": lifecycle.snapshot.flow_id,
            "action_id": ACTION_ID,
            "prepared_digest": "b" * 64,
            "wallet_pid": 404,
            "event": "REJECTED",
            "code": "LOCAL_CANCELLED",
            "outcome": None,
        })
        lifecycle.wallet_status_mismatch("WALLET_STATUS_MISMATCH")
        assert lifecycle.snapshot.state.value == "RECOVERY_REQUIRED"
        assert len(wallet.cancels) == 1


def test_ambiguous_wallet_preparation_requires_recovery():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()

        def ambiguous(_request):
            return WalletPreparedResult(
                False, "WALLET_PREPARATION_AMBIGUOUS", None, None,
            )

        wallet.prepare_transfer = ambiguous
        lifecycle, authority = service(Path(temporary), wallet)
        result = authority.handle(intent(), owner_pid=123)
        assert result.kind is MessageKind.RECOVERY_REQUIRED
        assert lifecycle.snapshot.state.value == "RECOVERY_REQUIRED"
        assert lifecycle.ledger.find(ACTION_ID).state.value == "RECOVERY_REQUIRED"
