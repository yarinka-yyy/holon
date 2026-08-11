from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from holon_contracts import ActionState, MessageKind, make_envelope, new_action_id
from holon_lending import ACTION_PROFILES_DIGEST, ActionProfilesState
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.authority import AuthorityService
from holon_guard.authority_audit import AuthorityAudit
from holon_guard.model import GuardResult
from holon_guard.request_control import RequestController
from holon_guard_ipc import GuardState
from holon_guard.wallet import (
    WALLET_OPEN_FAILURE_MESSAGES, WalletBalancesResult,
    WalletLendingPreviewResult, WalletOpenResult, WalletPreparedResult,
)
from holon_journal import EventType
from holon_policy import LendingRule, Policy, PolicyEngine, PolicySnapshot, policy_digest
from holon_wallet_control.lending_operation import LendingOperationStore
from guard_support import (
    ACTION_ID, ACTION_ID_2, enabled_policy, make_audit, make_ledger, transfer_request,
)


class Handle:
    pid = 202

    def __init__(self) -> None:
        self.exit_code: int | None = None

    def poll(self) -> int | None:
        return self.exit_code


class Wallet:
    def __init__(self) -> None:
        self.calls = 0
        self.open_calls = 0
        self.balance_calls = 0
        self.preview_calls = 0
        self.preview_payload = None
        self.lending_prepare_calls = 0
        self.lending_cancel_calls = 0
        self.close_calls = 0
        self.cancel_ok = True
        self.handle = Handle()

    def open_or_activate(self, flow_id: str) -> Handle:
        del flow_id
        self.calls += 1
        return self.handle

    def request_close(self, handle: Handle) -> None:
        self.close_calls += 1
        handle.exit_code = 0

    def open_public(self) -> WalletOpenResult:
        self.open_calls += 1
        return WalletOpenResult(
            True, "ACTIVATED", "WALLET_ACTIVATED", "Wallet is open.",
        )

    def read_public_balances(self) -> WalletBalancesResult:
        self.balance_calls += 1
        networks = [
            {
                "network": network, "chain_id": chain_id,
                "status": "UNAVAILABLE", "block_number": None,
                "updated_at": None, "error_code": "RPC_UNAVAILABLE",
                "balances": None,
            }
            for network, chain_id in (("ethereum", 1), ("base", 8453))
        ]
        return WalletBalancesResult(
            True,
            {
                "status": "DEGRADED", "authority_available": False,
                "account": {
                    "label": "Account 1",
                    "address": "0x1111111111111111111111111111111111111111",
                },
                "networks": networks, "code": "BALANCES_UNAVAILABLE",
                "message": "Wallet balances are unavailable.",
            },
        )

    def preview_lending(self, intent, profile_digest) -> WalletLendingPreviewResult:
        from holon_lending.preflight import unavailable_preview

        self.preview_calls += 1
        if self.preview_payload is not None:
            return WalletLendingPreviewResult(True, dict(self.preview_payload))
        return WalletLendingPreviewResult(
            True,
            unavailable_preview(
                "BASE_RPC_UNAVAILABLE", requested_action=intent["action"],
                amount_mode=intent["amount_mode"], profile_digest=profile_digest,
            ),
        )

    def prepare_lending_action(self, request) -> WalletPreparedResult:
        self.lending_prepare_calls += 1
        next_action = "withdraw" if request["action"] == "withdraw" else "approve"
        profile = ActionProfilesState.load().select(request["protocol_profile_id"])
        assert profile is not None
        selectors = {
            "aave-v3-base-usdc": "0x69328dec",
            "compound-v3-base-usdc": "0xf3fef3a3",
            "morpho-v1-gauntlet-usdc-prime": "0xb460af94",
        }
        target = profile.asset if next_action == "approve" else profile.target
        return WalletPreparedResult(True, "LENDING_ACTION_PREPARED", {
            "amount_atomic": "1000000",
            "max_total_fee_wei": "90000000000000",
            "prepared_digest": "a" * 64,
            "next_action": next_action, "profile_id": "profile-one",
            "sender": "0x1111111111111111111111111111111111111111",
            "network": "base", "asset": "usdc", "target": target,
            "selector": selectors[profile.profile_id] if next_action == "withdraw" else "0x095ea7b3",
            "calldata_hash": "c" * 64,
        }, self.handle)

    def cancel_transfer(self, request) -> bool:
        del request
        self.lending_cancel_calls += 1
        self.handle.exit_code = 0
        return self.cancel_ok


class Owner:
    alive = True

    def is_alive(self, pid: int) -> bool:
        del pid
        return self.alive


def lending_request(
    action_id: str = ACTION_ID, *,
    profile_id: str = "aave-v3-base-usdc",
    action: str = "supply", amount_mode: str = "exact",
    amount: str | None = "1",
):
    return make_envelope(
        MessageKind.LENDING_AUTHORITY_INTENT,
        {
            "module_id": "lending", "module_version": "1",
            "protocol_profile_id": profile_id,
            "protocol_profile_version": "1", "network": "base", "asset": "usdc",
            "beneficiary_mode": "active_wallet_account", "action": action,
            "amount_mode": amount_mode, "amount": amount,
        },
        action_id=action_id,
    )


def module_request(action_id: str):
    return make_envelope(
        MessageKind.MODULE_AUTHORITY_INTENT,
        {
            "module_id": "holon.perpdex",
            "capability_id": "holon.perpdex.action.guard",
            "action_type": "OPEN_POSITION", "params": {},
            "preview_digest": "a" * 64,
        },
        action_id=action_id,
    )


class AuthorityTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        root = Path(self.temporary.name)
        store = SnapshotStore(root / "guard-state.json")
        store.bootstrap_normal_for_test(1.0)
        self.wallet = Wallet()
        self.lifecycle = GuardLifecycle(
            store, store.load(), self.wallet, Owner(), make_ledger(root)
        )
        self.audit = make_audit(root)
        self.service = AuthorityService(self.lifecycle, enabled_policy(), self.audit)

    def lending_service(self, policy: Policy | None = None) -> AuthorityService:
        selected = policy or Policy("4", "3", False, ())
        snapshot = PolicySnapshot(
            2, policy_digest(selected.to_dict()), selected, "d" * 64,
        )
        return AuthorityService(
            self.lifecycle, PolicyEngine(selected), self.audit,
            policy_snapshot=snapshot, lending_actions=ActionProfilesState.load(),
        )

    def test_policy_refusal_never_starts_wallet_or_action(self) -> None:
        refused = self.service.handle(transfer_request(network="ethereum"), owner_pid=101)
        self.assertEqual(refused.kind, MessageKind.REFUSAL)
        self.assertEqual(refused.payload["code"], "NETWORK_NOT_ALLOWED")
        self.assertEqual(self.wallet.calls, 0)
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)
        self.assertEqual(self.lifecycle.ledger.find(refused.action_id).state.value, "REFUSED")

    def test_prepare_status_mutation_and_one_active_action(self) -> None:
        started = self.service.handle(transfer_request(), owner_pid=101)
        self.assertEqual(started.kind, MessageKind.PROTECTED_FLOW_STARTED)
        status_request = replace(
            transfer_request(), kind=MessageKind.ACTION_STATUS_REQUEST, payload={}
        )
        status = self.service.handle(status_request, owner_pid=None)
        self.assertEqual(status.payload["action_state"], "AWAITING_LOCAL_CONFIRMATION")
        wrong_cancel = replace(
            status_request, kind=MessageKind.CANCEL_ACTION, action_id=ACTION_ID_2
        )
        self.assertEqual(
            self.service.handle(wrong_cancel, owner_pid=None).payload["code"],
            "ACTION_ID_INVALID",
        )
        mutated = self.service.handle(
            transfer_request(amount_atomic="999999"), owner_pid=101
        )
        self.assertEqual(mutated.payload["code"], "ACTION_MUTATED")
        other = self.service.handle(transfer_request(ACTION_ID_2), owner_pid=101)
        self.assertEqual(other.payload["code"], "ACTION_ALREADY_ACTIVE")

    def test_unexpected_clean_exit_requires_recovery_and_replay_stays_blocked(self) -> None:
        self.service.handle(transfer_request(), owner_pid=101)
        self.wallet.handle.exit_code = 0
        result = self.lifecycle.monitor_once()
        self.assertEqual(result.state.value, "RECOVERY_REQUIRED")
        request = replace(transfer_request(), kind=MessageKind.RECOVER_ACTION, payload={})
        recovered = self.service.handle(request, owner_pid=None)
        self.assertEqual(recovered.payload["guard_state"], "NORMAL")
        replay = self.service.handle(transfer_request(), owner_pid=101)
        self.assertEqual(replay.payload["code"], "ACTION_REPLAYED")

    def test_disabled_policy_is_a_refusal_not_a_technical_error(self) -> None:
        disabled = Policy("2", "1", False, ())
        service = AuthorityService(self.lifecycle, PolicyEngine(disabled), self.audit)
        response = service.handle(transfer_request(), owner_pid=101)
        self.assertEqual(response.kind, MessageKind.REFUSAL)
        self.assertEqual(response.payload["code"], "POLICY_AUTHORITY_DISABLED")

    def test_health_does_not_echo_untrusted_persisted_reason(self) -> None:
        self.lifecycle.disable_signing("PRIVATE_SECRET")
        request = make_envelope(MessageKind.HEALTH_REQUEST, {})
        response = self.service.handle(request, owner_pid=None)
        self.assertEqual(response.payload["code"], "SIGNING_DISABLED")
        self.assertNotIn("PRIVATE_SECRET", str(response.to_dict()))

    def test_public_open_preserves_guard_state_and_creates_no_action(self) -> None:
        request = make_envelope(MessageKind.OPEN_WALLET, {})
        opened = self.service.handle(request, owner_pid=None)
        self.assertEqual(opened.kind, MessageKind.WALLET_OPENED)
        self.assertEqual(opened.payload["wallet_state"], "ACTIVATED")
        self.assertEqual(opened.payload["code"], "WALLET_ACTIVATED")
        self.assertEqual(
            opened.payload["message"], "Wallet activation was requested.",
        )
        self.assertFalse(opened.payload["authority_available"])
        self.assertEqual(self.lifecycle.snapshot.state.value, "NORMAL")
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)
        self.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        opened_disabled = self.service.handle(request, owner_pid=None)
        self.assertEqual(opened_disabled.kind, MessageKind.WALLET_OPENED)
        self.assertEqual(opened_disabled.payload["guard_state"], "SIGNING_DISABLED")
        self.assertEqual(self.wallet.open_calls, 2)

    def test_public_open_failure_has_safe_internal_code_and_audit(self) -> None:
        def fail():
            raise RuntimeError("private path and process detail")

        self.wallet.open_public = fail  # type: ignore[method-assign]
        response = self.service.handle(make_envelope(MessageKind.OPEN_WALLET, {}), None)
        self.assertEqual(response.kind, MessageKind.ERROR)
        self.assertEqual(response.payload["code"], "WALLET_OPEN_INTERNAL_FAILURE")
        serialized = str(response.to_dict()).lower()
        for value in ("private", "path", "process", "detail"):
            self.assertNotIn(value, serialized)
        events = [
            event for event in self.audit.journal.events()
            if event.event_type is EventType.TECHNICAL_ERROR
        ]
        self.assertEqual([event.code for event in events], [
            "WALLET_OPEN_INTERNAL_FAILURE",
        ])
        self.assertEqual(self.lifecycle.snapshot.state.value, "NORMAL")
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)

    def test_public_open_passes_only_known_safe_startup_failure(self) -> None:
        for code, safe_message in WALLET_OPEN_FAILURE_MESSAGES.items():
            with self.subTest(code=code):
                exit_code = 7 if code == "WALLET_EXITED" else None
                self.wallet.open_public = lambda code=code, exit_code=exit_code: (  # type: ignore[method-assign]
                    WalletOpenResult(
                        False, "", code, "private path pipe pid", exit_code,
                    )
                )

                response = self.service.handle(
                    make_envelope(MessageKind.OPEN_WALLET, {}), None,
                )

                self.assertEqual(response.kind, MessageKind.ERROR)
                self.assertEqual(response.payload["code"], code)
                expected_message = (
                    "Wallet exited before it became ready (exit code 7)."
                    if code == "WALLET_EXITED" else safe_message
                )
                self.assertEqual(response.payload["message"], expected_message)
                serialized = str(response.to_dict()).lower()
                for value in ("private", "path", "pipe", "pid"):
                    self.assertNotIn(value, serialized)

    def test_public_balances_preserve_guard_state_and_create_no_action(self) -> None:
        request = make_envelope(MessageKind.READ_WALLET_BALANCES, {})
        response = self.service.handle(request, owner_pid=None)
        self.assertEqual(response.kind, MessageKind.WALLET_BALANCES)
        self.assertEqual(response.payload["status"], "DEGRADED")
        self.assertFalse(response.payload["authority_available"])
        self.assertNotIn("action_id", response.to_dict())
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)
        self.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        disabled = self.service.handle(request, owner_pid=None)
        self.assertEqual(disabled.kind, MessageKind.WALLET_BALANCES)

    def test_public_balance_failure_is_generic(self) -> None:
        def fail():
            raise RuntimeError("private endpoint and query detail")

        self.wallet.read_public_balances = fail  # type: ignore[method-assign]
        response = self.service.handle(
            make_envelope(MessageKind.READ_WALLET_BALANCES, {}), None,
        )
        self.assertEqual(response.kind, MessageKind.ERROR)
        self.assertEqual(response.payload["code"], "WALLET_BALANCES_UNAVAILABLE")
        self.assertNotIn("private", str(response.to_dict()).lower())

    def test_module_fresh_prepare_exposes_only_safe_diagnostic_categories(self) -> None:
        class AdapterError(RuntimeError):
            def __init__(self, code: str) -> None:
                self.code = code

        class Adapter:
            def __init__(self, error: Exception) -> None:
                self.error = error

            def prepare(self, *_args):
                raise self.error

        def unavailable_adapter(*_args):
            raise RuntimeError("private adapter detail")

        self.service.module_action_adapter = unavailable_adapter  # type: ignore[method-assign]
        unavailable = self.service.handle(module_request(new_action_id()), owner_pid=101)
        self.assertEqual(unavailable.payload["code"], "MODULE_ADAPTER_UNAVAILABLE")
        self.assertEqual(unavailable.payload["stage"], "GUARD_FRESH_PREPARE")
        self.assertEqual(
            self.audit.journal.events()[-1].public_fields["failure_category"], "adapter",
        )

        cases = (
            (AdapterError("HYPERLIQUID_UNAVAILABLE"), "HYPERLIQUID_UNAVAILABLE", "public_transport"),
            (AdapterError("PERPDEX_NONCE_STATE_INVALID"), "PERPDEX_NONCE_STATE_INVALID", "perpdex_state"),
            (ValueError("private local detail"), "MODULE_ACTION_INTERNAL_FAILURE", "internal"),
        )
        for error, code, category in cases:
            with self.subTest(code=code):
                self.service.module_action_adapter = lambda *_args, error=error: (  # type: ignore[method-assign]
                    object(), Adapter(error),
                )
                response = self.service.handle(module_request(new_action_id()), owner_pid=101)
                self.assertEqual(response.kind, MessageKind.REFUSAL)
                self.assertEqual(response.payload["code"], code)
                self.assertEqual(response.payload["stage"], "GUARD_FRESH_PREPARE")
                event = self.audit.journal.events()[-1]
                self.assertEqual(event.event_type, EventType.TECHNICAL_ERROR)
                self.assertEqual(event.public_fields["failure_category"], category)
                self.assertNotIn("private", str(response.to_dict()).lower())

    def test_module_account_refusal_has_safe_guard_stage(self) -> None:
        self.service.module_action_adapter = lambda *_args: (object(), object())  # type: ignore[method-assign]
        self.wallet.read_public_balances = lambda: WalletBalancesResult(  # type: ignore[method-assign]
            True, {"account": None},
        )
        response = self.service.handle(module_request(new_action_id()), owner_pid=101)
        self.assertEqual(response.kind, MessageKind.REFUSAL)
        self.assertEqual(response.payload["code"], "WALLET_ACCOUNT_UNAVAILABLE")
        self.assertEqual(response.payload["stage"], "GUARD_FRESH_PREPARE")
        event = self.audit.journal.events()[-1]
        self.assertEqual(event.public_fields["failure_category"], "wallet")

        def unavailable_account():
            raise RuntimeError("private Wallet process detail")

        self.wallet.read_public_balances = unavailable_account  # type: ignore[method-assign]
        raised = self.service.handle(module_request(new_action_id()), owner_pid=101)
        self.assertEqual(raised.payload["code"], "WALLET_ACCOUNT_UNAVAILABLE")
        self.assertEqual(raised.payload["stage"], "GUARD_FRESH_PREPARE")
        self.assertNotIn("private", str(raised.to_dict()).lower())

    def test_wallet_live_verify_failure_writes_safe_stage_diagnostic(self) -> None:
        class Bundle:
            account = "0x1111111111111111111111111111111111111111"

            @staticmethod
            def to_mapping():
                return {}

        class Adapter:
            wallet_capability_id = "holon.perpdex.action.wallet"

            @staticmethod
            def prepare(*_args):
                return Bundle()

            @staticmethod
            def reject(*_args):
                return None

        capability = SimpleNamespace(
            module_id="holon.perpdex",
            declaration=SimpleNamespace(descriptor={"profile_id": "hyperliquid-mainnet-v1"}),
        )
        self.service.module_action_adapter = lambda *_args: (capability, Adapter())  # type: ignore[method-assign]
        self.lifecycle.start_module_intent = lambda *_args: (  # type: ignore[method-assign]
            GuardResult(
                False, "HYPERLIQUID_UNAVAILABLE", GuardState.NORMAL,
                "Private transport exception", None, "WALLET_LIVE_VERIFY",
            ),
            None,
        )
        response = self.service.handle(module_request(new_action_id()), owner_pid=101)
        self.assertEqual(response.kind, MessageKind.ERROR)
        self.assertEqual(response.payload["code"], "HYPERLIQUID_UNAVAILABLE")
        self.assertEqual(response.payload["stage"], "WALLET_LIVE_VERIFY")
        event = self.audit.journal.events()[-1]
        self.assertEqual(event.event_type, EventType.TECHNICAL_ERROR)
        self.assertEqual(event.public_fields["failure_category"], "public_transport")
        self.assertNotIn("private", str(event.to_dict()).lower())

    def test_lending_reads_are_public_and_work_when_signing_disabled(self) -> None:
        from holon_lending import (
            LendingAnalyticsStore, LendingPortfolioService, LendingReadService,
        )

        self.service.lending = LendingReadService.unavailable()
        compare = self.service.handle(
            make_envelope(MessageKind.READ_LENDING_MARKETS, {}), None,
        )
        self.assertEqual(compare.kind, MessageKind.LENDING_MARKETS)
        self.assertEqual(compare.payload["code"], "LENDING_UNAVAILABLE")
        self.assertEqual(self.wallet.balance_calls, 0)

        self.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        positions = self.service.handle(
            make_envelope(MessageKind.READ_LENDING_POSITIONS, {}), None,
        )
        self.assertEqual(positions.kind, MessageKind.LENDING_POSITIONS)
        self.assertEqual(positions.payload["code"], "LENDING_POSITIONS_UNAVAILABLE")
        self.assertEqual(self.wallet.balance_calls, 1)
        self.service.lending_portfolio = LendingPortfolioService(
            LendingReadService.unavailable(),
            LendingAnalyticsStore(
                Path(self.temporary.name) / "lending-analytics.json",
            ),
        )
        portfolio = self.service.handle(
            make_envelope(
                MessageKind.READ_LENDING_PORTFOLIO,
                {"force_refresh": False, "history_period": "7d"},
            ),
            None,
        )
        self.assertEqual(portfolio.kind, MessageKind.LENDING_PORTFOLIO)
        self.assertEqual(portfolio.payload["code"], "LENDING_PORTFOLIO_UNAVAILABLE")
        self.assertEqual(portfolio.payload["history"]["period"], "7d")
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)

    def test_earn_portfolio_is_public_normalized_and_creates_no_action(self) -> None:
        from holon_earn import (
            EarnPortfolioService, EarnProviderRegistry, EarnSnapshotStore,
            LendingEarnProvider,
        )
        from holon_lending import (
            LendingAnalyticsStore, LendingPortfolioService, LendingReadService,
        )

        root = Path(self.temporary.name)
        lending = LendingPortfolioService(
            LendingReadService.unavailable(),
            LendingAnalyticsStore(root / "lending-analytics.json"),
        )
        registry = EarnProviderRegistry()
        registry.register(LendingEarnProvider(lending))
        self.service.lending_portfolio = lending
        self.service.earn_portfolio = EarnPortfolioService(
            registry, EarnSnapshotStore(root / "earn-snapshots.json"),
        )
        self.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")

        response = self.service.handle(
            make_envelope(
                MessageKind.READ_EARN_PORTFOLIO, {"force_refresh": False},
            ),
            None,
        )

        self.assertEqual(response.kind, MessageKind.EARN_PORTFOLIO)
        self.assertEqual(response.payload["earn_schema_version"], "1")
        self.assertFalse(response.payload["authority_available"])
        self.assertFalse(response.payload["total_complete"])
        self.assertEqual(response.payload["providers"][0]["provider_id"], "holon.lending")
        self.assertTrue(all(
            product["risk"]["state"] == "NOT_ASSESSED"
            for product in response.payload["providers"][0]["products"]
        ))
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)

    def test_lending_preview_works_when_signing_disabled_without_action_state(self) -> None:
        request = make_envelope(
            MessageKind.LENDING_ACTION_INTENT,
            {
                "module_id": "lending", "module_version": "1",
                "protocol_profile_id": "aave-v3-base-usdc",
                "protocol_profile_version": "1", "network": "base", "asset": "usdc",
                "beneficiary_mode": "active_wallet_account", "action": "supply",
                "amount_mode": "exact", "amount": "1",
            },
        )
        self.lifecycle.disable_signing("POLICY_AUTHORITY_DISABLED")
        response = self.service.handle(request, owner_pid=None)
        self.assertEqual(response.kind, MessageKind.LENDING_ACTION_PREVIEW)
        self.assertEqual(response.payload["status"], "UNAVAILABLE")
        self.assertEqual(self.wallet.preview_calls, 1)
        self.assertIsNone(self.lifecycle.ledger.snapshot.current)
        self.assertNotIn("action_id", response.to_dict())

    def test_lending_authority_starts_built_in_protected_action_under_v4(self) -> None:
        service = self.lending_service()
        request = lending_request()
        response = service.handle(request, owner_pid=101)
        self.assertEqual(response.kind, MessageKind.PROTECTED_FLOW_STARTED)
        self.assertEqual(self.wallet.lending_prepare_calls, 1)
        self.assertEqual(self.lifecycle.snapshot.state.value, "ACTIVE")
        self.assertEqual(
            service.handle(request, owner_pid=101).payload["code"], "ACTION_REPLAYED",
        )

    def test_lending_operation_write_failure_cancels_prepared_wallet_action(self) -> None:
        root = Path(self.temporary.name)
        operation_store = LendingOperationStore(root / "lending-operation-state.json")
        self.lifecycle.lending_operation_store = operation_store
        self.lifecycle.lending_operation_snapshot = operation_store.load()
        service = self.lending_service()
        with patch.object(operation_store, "save", side_effect=OSError("canary")):
            response = service.handle(lending_request(), owner_pid=101)
        self.assertEqual(response.kind, MessageKind.SIGNING_DISABLED)
        self.assertEqual(response.payload["code"], "LENDING_OPERATION_STATE_INVALID")
        self.assertEqual(self.wallet.lending_cancel_calls, 1)
        self.assertIsNone(self.lifecycle.wallet_handle)
        self.assertIsNone(self.lifecycle.prepared_digest)

    def test_ambiguous_lending_cancellation_preserves_recovery(self) -> None:
        root = Path(self.temporary.name)
        operation_store = LendingOperationStore(root / "lending-operation-state.json")
        self.lifecycle.lending_operation_store = operation_store
        self.lifecycle.lending_operation_snapshot = operation_store.load()
        self.wallet.cancel_ok = False
        service = self.lending_service()
        with patch.object(operation_store, "save", side_effect=OSError("canary")):
            response = service.handle(lending_request(), owner_pid=101)
        self.assertEqual(response.kind, MessageKind.RECOVERY_REQUIRED)
        self.assertEqual(self.lifecycle.snapshot.state.value, "RECOVERY_REQUIRED")
        self.assertEqual(self.wallet.lending_cancel_calls, 1)
        self.assertEqual(self.wallet.close_calls, 1)
        self.assertFalse(self.lifecycle.accept_wallet_status({
            "flow_id": self.lifecycle.snapshot.flow_id,
            "action_id": ACTION_ID,
            "prepared_digest": "a" * 64,
            "wallet_pid": 202,
        }))
        restored = GuardLifecycle.restore(
            self.lifecycle.store, self.wallet, Owner(), self.lifecycle.ledger,
            operation_store, self.lifecycle.lending_operation_snapshot,
        )
        self.assertEqual(restored.snapshot.state.value, "RECOVERY_REQUIRED")

    def test_lending_third_equivalent_request_persists_global_block(self) -> None:
        now = [100.0]
        self.audit.requests.clock = lambda: now[0]
        service = self.lending_service()
        first = service.handle(lending_request(), owner_pid=101)
        second = service.handle(
            lending_request("act-44444444-4444-4444-8444-444444444444"),
            owner_pid=101,
        )
        third = service.handle(
            lending_request("act-55555555-5555-4555-8555-555555555555"),
            owner_pid=101,
        )
        self.assertEqual(first.kind, MessageKind.PROTECTED_FLOW_STARTED)
        self.assertEqual(second.payload["code"], "ACTION_ALREADY_ACTIVE")
        self.assertEqual(third.payload["code"], "REQUEST_TEMPORARILY_BLOCKED")
        self.assertEqual(self.wallet.lending_prepare_calls, 1)
        self.assertEqual(self.wallet.lending_cancel_calls, 1)
        self.assertEqual(self.lifecycle.snapshot.state.value, "RECOVERY_REQUIRED")
        states = {
            record.action_id: record.state
            for record in self.lifecycle.ledger.snapshot.terminal
        }
        self.assertEqual(states[third.action_id], ActionState.REFUSED)
        self.assertEqual(states[first.action_id], ActionState.RECOVERY_REQUIRED)
        block_events = [
            event for event in self.audit.journal.events()
            if event.event_type is EventType.REQUEST_BLOCK_STARTED
        ]
        self.assertEqual(len(block_events), 1)
        self.assertEqual(
            block_events[0].public_fields["recipient"],
            ActionProfilesState.load().select("aave-v3-base-usdc").target,
        )

        self.audit.requests = RequestController(
            self.audit.requests.store, self.audit.requests.store.load(),
            clock=lambda: now[0],
        )
        different = service.handle(
            lending_request(
                "act-66666666-6666-4666-8666-666666666666", amount="2",
            ),
            owner_pid=101,
        )
        self.assertEqual(different.payload["code"], "REQUEST_TEMPORARILY_BLOCKED")

        recovery = make_envelope(
            MessageKind.RECOVER_ACTION, {}, action_id=first.action_id,
        )
        self.assertEqual(service.handle(recovery, None).payload["guard_state"], "NORMAL")
        now[0] = 401.0
        resumed = service.handle(
            lending_request(
                "act-77777777-7777-4777-8777-777777777777", amount="2",
            ),
            owner_pid=101,
        )
        self.assertEqual(resumed.kind, MessageKind.PROTECTED_FLOW_STARTED)
        self.assertIn(
            EventType.REQUEST_BLOCK_EXPIRED,
            {event.event_type for event in self.audit.journal.events()},
        )

    def test_lending_request_control_write_failure_fails_closed_before_wallet(self) -> None:
        service = self.lending_service()
        with patch.object(
            self.audit.requests.store, "save", side_effect=OSError("canary"),
        ):
            response = service.handle(lending_request(), owner_pid=101)
        self.assertEqual(response.payload["code"], "REQUEST_CONTROL_STATE_INVALID")
        self.assertEqual(self.lifecycle.snapshot.state.value, "SIGNING_DISABLED")
        self.assertEqual(self.wallet.lending_prepare_calls, 0)

    def test_lending_refusal_ledger_failures_disable_signing(self) -> None:
        disabled_v3 = Policy("3", "2", False, (), False, ())
        service = self.lending_service(disabled_v3)
        with patch.object(
            self.lifecycle.ledger.store, "save", side_effect=OSError("canary"),
        ):
            preliminary = service.handle(lending_request(), owner_pid=101)
        self.assertEqual(preliminary.payload["code"], "ACTION_STATE_INVALID")
        self.assertEqual(self.wallet.lending_prepare_calls, 0)
        self.assertEqual(
            service.handle(lending_request(), owner_pid=101).payload["code"],
            "ACTION_STATE_INVALID",
        )

    def test_lending_all_preview_refusal_ledger_failure_disables_signing(self) -> None:
        service = self.lending_service()
        with patch.object(
            self.lifecycle.ledger.store, "save", side_effect=OSError("canary"),
        ):
            response = service.handle(
                lending_request(action="withdraw", amount_mode="all", amount=None),
                owner_pid=101,
            )
        self.assertEqual(response.payload["code"], "ACTION_STATE_INVALID")
        self.assertEqual(self.wallet.lending_prepare_calls, 0)

    def test_lending_final_policy_refusal_ledger_failure_disables_signing(self) -> None:
        rule = LendingRule(
            "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
            ("withdraw",), "500000", "100000000000000",
            ACTION_PROFILES_DIGEST,
        )
        policy = Policy("3", "2", False, (), True, (rule,))
        service = self.lending_service(policy)
        self.wallet.preview_payload = {
            "status": "PREVIEW_READY", "requested_action": "withdraw",
            "amount_mode": "all", "amount_atomic": "999999",
        }
        with patch.object(
            self.lifecycle.ledger.store, "save", side_effect=OSError("canary"),
        ):
            response = service.handle(
                lending_request(action="withdraw", amount_mode="all", amount=None),
                owner_pid=101,
            )
        self.assertEqual(response.payload["code"], "ACTION_STATE_INVALID")
        self.assertEqual(self.wallet.lending_prepare_calls, 0)

    def test_lending_journal_target_matches_selected_profile(self) -> None:
        profiles = ActionProfilesState.load()
        for profile in profiles.profiles:
            with self.subTest(profile=profile.profile_id):
                fields = AuthorityAudit.transfer_fields(
                    lending_request(profile_id=profile.profile_id),
                    amount_atomic="1000000", policy_version="3",
                )
                self.assertEqual(fields["recipient"], profile.target)

    def test_lending_wallet_status_journal_covers_all_protocol_targets(self) -> None:
        profiles = ActionProfilesState.load()
        for index, profile in enumerate(profiles.profiles):
            with self.subTest(profile=profile.profile_id):
                root = Path(self.temporary.name) / f"journal-{index}"
                root.mkdir()
                store = SnapshotStore(root / "guard-state.json")
                store.bootstrap_normal_for_test(1.0)
                wallet = Wallet()
                lifecycle = GuardLifecycle(
                    store, store.load(), wallet, Owner(), make_ledger(root),
                )
                audit = make_audit(root)
                policy = Policy("4", "3", False, ())
                service = AuthorityService(
                    lifecycle, PolicyEngine(policy), audit,
                    policy_snapshot=PolicySnapshot(
                        2, policy_digest(policy.to_dict()), policy, "d" * 64,
                    ),
                    lending_actions=profiles,
                )
                response = service.handle(
                    lending_request(
                        profile_id=profile.profile_id, action="withdraw",
                        amount_mode="exact", amount="1",
                    ),
                    owner_pid=101,
                )
                self.assertEqual(response.kind, MessageKind.PROTECTED_FLOW_STARTED)
                transaction_hash = "0x" + str(index + 1) * 64
                update = {
                    "flow_id": lifecycle.snapshot.flow_id,
                    "action_id": lifecycle.snapshot.action_id,
                    "prepared_digest": lifecycle.prepared_digest,
                    "wallet_pid": 202, "event": "COMPLETED", "code": "PENDING",
                    "transaction_hash": transaction_hash,
                    "receipt_state": "pending", "outcome": "pending",
                }
                self.assertTrue(service.accept_wallet_status(update))
                contract_events = [
                    event for event in audit.journal.events()
                    if event.event_type is EventType.CONTRACT_ACTION
                ]
                self.assertEqual(len(contract_events), 1)
                self.assertEqual(
                    contract_events[0].public_fields["wallet_address"],
                    "0x1111111111111111111111111111111111111111",
                )
                self.assertEqual(contract_events[0].public_fields["asset"], "usdc")
                self.assertEqual(
                    contract_events[0].public_fields["amount_atomic"], "1000000",
                )
                self.assertEqual(contract_events[0].public_fields["contract"], profile.target)
                expected_selector = {
                    "aave-v3-base-usdc": "0x69328dec",
                    "compound-v3-base-usdc": "0xf3fef3a3",
                    "morpho-v1-gauntlet-usdc-prime": "0xb460af94",
                }[profile.profile_id]
                self.assertEqual(contract_events[0].public_fields["selector"], expected_selector)
                broadcast_events = [
                    event for event in audit.journal.events()
                    if event.event_type is EventType.BROADCAST_RESULT
                ]
                self.assertEqual(len(broadcast_events), 1)
                self.assertEqual(
                    broadcast_events[-1].public_fields["transaction_hash"],
                    transaction_hash,
                )

    def test_lending_journal_records_broadcast_and_both_receipt_outcomes(self) -> None:
        for index, terminal_event in enumerate(("RECEIPT_CONFIRMED", "RECEIPT_FAILED")):
            with self.subTest(event=terminal_event):
                root = Path(self.temporary.name) / f"receipt-{index}"
                root.mkdir()
                store = SnapshotStore(root / "guard-state.json")
                store.bootstrap_normal_for_test(1.0)
                wallet = Wallet()
                lifecycle = GuardLifecycle(
                    store, store.load(), wallet, Owner(), make_ledger(root),
                )
                operation_store = LendingOperationStore(
                    root / "lending-operation-state.json",
                )
                lifecycle.lending_operation_store = operation_store
                lifecycle.lending_operation_snapshot = operation_store.load()
                audit = make_audit(root)
                policy = Policy("4", "3", False, ())
                service = AuthorityService(
                    lifecycle, PolicyEngine(policy), audit,
                    policy_snapshot=PolicySnapshot(
                        2, policy_digest(policy.to_dict()), policy, "d" * 64,
                    ),
                    lending_actions=ActionProfilesState.load(),
                )
                response = service.handle(
                    lending_request(
                        action="supply", amount_mode="exact", amount="1",
                    ),
                    owner_pid=101,
                )
                self.assertEqual(response.kind, MessageKind.PROTECTED_FLOW_STARTED)
                operation = lifecycle.lending_operation_snapshot.current
                self.assertIsNotNone(operation)
                transaction_hash = "0x" + str(index + 7) * 64
                common = {
                    "flow_id": lifecycle.snapshot.flow_id,
                    "action_id": lifecycle.snapshot.action_id,
                    "prepared_digest": lifecycle.prepared_digest,
                    "wallet_pid": 202,
                    "operation_id": operation.operation_id,
                    "phase_action_id": operation.phase_action_id,
                    "phase": "approve",
                    "transaction_hash": transaction_hash,
                }
                self.assertTrue(service.accept_wallet_status({
                    **common, "event": "BROADCASTED",
                    "code": "BROADCAST_SUBMITTED", "receipt_state": "pending",
                    "outcome": "pending",
                }))
                self.assertTrue(service.accept_wallet_status({
                    **common, "event": terminal_event, "code": terminal_event,
                    "receipt_state": (
                        "confirmed" if terminal_event == "RECEIPT_CONFIRMED" else "failed"
                    ),
                    "outcome": (
                        "success" if terminal_event == "RECEIPT_CONFIRMED" else "failed"
                    ),
                }))
                broadcasts = [
                    event for event in audit.journal.events()
                    if event.event_type is EventType.BROADCAST_RESULT
                ]
                self.assertEqual([event.code for event in broadcasts], [
                    "BROADCAST_SUBMITTED", terminal_event,
                ])
                self.assertTrue(all(
                    event.public_fields["transaction_hash"] == transaction_hash
                    for event in broadcasts
                ))

    def test_withdraw_all_resolves_position_before_starting_protected_flow(self) -> None:
        rule = LendingRule(
            "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
            ("withdraw",), "1010000", "100000000000000",
            ACTION_PROFILES_DIGEST,
        )
        policy = Policy("3", "2", False, (), True, (rule,))
        snapshot = PolicySnapshot(3, policy_digest(policy.to_dict()), policy, "d" * 64)
        service = AuthorityService(
            self.lifecycle, PolicyEngine(policy), self.audit,
            policy_snapshot=snapshot, lending_actions=ActionProfilesState.load(),
        )
        self.wallet.preview_payload = {
            "status": "PREVIEW_READY", "requested_action": "withdraw",
            "amount_mode": "all", "amount_atomic": "999999",
        }
        request = make_envelope(
            MessageKind.LENDING_AUTHORITY_INTENT,
            {
                "module_id": "lending", "module_version": "1",
                "protocol_profile_id": "aave-v3-base-usdc",
                "protocol_profile_version": "1", "network": "base", "asset": "usdc",
                "beneficiary_mode": "active_wallet_account", "action": "withdraw",
                "amount_mode": "all", "amount": None,
            }, action_id=ACTION_ID,
        )
        response = service.handle(request, owner_pid=101)
        self.assertEqual(response.kind, MessageKind.PROTECTED_FLOW_STARTED)
        self.assertEqual(self.wallet.preview_calls, 1)
        self.assertEqual(self.wallet.lending_prepare_calls, 1)
        replay = service.handle(request, owner_pid=101)
        self.assertEqual(replay.payload["code"], "ACTION_REPLAYED")
        self.assertEqual(self.wallet.preview_calls, 1)


if __name__ == "__main__":
    unittest.main()
