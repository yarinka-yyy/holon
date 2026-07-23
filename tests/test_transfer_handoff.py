from __future__ import annotations

import tempfile
from pathlib import Path

from holon_contracts import MessageKind, make_envelope
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.authority import AuthorityService
from holon_guard.wallet import WalletPreparedResult
from holon_policy import Policy, PolicyEngine
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
        return WalletPreparedResult(True, "TRANSFER_PREPARED", {
            "authority_version": "1", "kind": "transfer_prepared",
            "flow_id": request["flow_id"], "action_id": request["action_id"],
            "wallet_pid": self.handle.pid, "profile_id": "profile-one",
            "sender": "0x2222222222222222222222222222222222222222",
            "recipient": request["recipient"], "network": request["network"],
            "asset": request["asset"], "amount_atomic": request["amount_atomic"],
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
        assert "max_total_fee_wei" not in intent().payload
        assert lifecycle.accept_wallet_status({
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


def test_disabled_policy_and_amount_cap_refuse_before_wallet():
    with tempfile.TemporaryDirectory() as temporary:
        wallet = AuthorityWallet()
        disabled = PolicyEngine(Policy("1", "1", False, ()))
        lifecycle, authority = service(Path(temporary), wallet, disabled)
        result = authority.handle(intent(), owner_pid=123)
        assert result.payload["code"] == "POLICY_AUTHORITY_DISABLED"
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
