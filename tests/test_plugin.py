from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from holon_hermes_plugin import plugin
from holon_hermes_plugin.guard import GuardHealth, GuardState
from holon_contracts import MessageKind, make_envelope


class StaticConnector:
    def __init__(self, health: GuardHealth) -> None:
        self.health = health
        self.ensure_calls = 0
        self.action_state = "AWAITING_LOCAL_CONFIRMATION"

    def probe(self) -> GuardHealth:
        return self.health

    def ensure_available(self) -> GuardHealth:
        self.ensure_calls += 1
        return self.health

    def open_wallet(self):
        return make_envelope(
            MessageKind.WALLET_OPENED,
            {
                "guard_state": self.health.state.value,
                "authority_available": False,
                "wallet_state": "ACTIVATED",
                "code": "WALLET_ACTIVATED",
                "message": "Wallet is open.",
            },
        )

    def wallet_balances(self):
        return make_envelope(
            MessageKind.WALLET_BALANCES,
            {
                "status": "PARTIAL",
                "authority_available": False,
                "account": {
                    "label": "Account 1",
                    "address": "0x1111111111111111111111111111111111111111",
                },
                "networks": [
                    {
                        "network": "ethereum", "chain_id": 1, "status": "LIVE",
                        "block_number": "123", "updated_at": "2026-07-23T12:00:00Z",
                        "error_code": None,
                        "balances": {
                            "ETH": {"asset": "ETH", "amount_atomic": "0", "decimals": 18, "display": "0 ETH"},
                            "USDC": {"asset": "USDC", "amount_atomic": "0", "decimals": 6, "display": "0 USDC"},
                        },
                    },
                    {
                        "network": "base", "chain_id": 8453,
                        "status": "UNAVAILABLE", "block_number": None,
                        "updated_at": None, "error_code": "RPC_TIMEOUT",
                        "balances": None,
                    },
                ],
                "code": "BALANCES_PARTIAL",
                "message": "Some Wallet balances are unavailable.",
            },
        )

    def lending_markets(self):
        from holon_lending import LendingReadService
        return make_envelope(
            MessageKind.LENDING_MARKETS,
            LendingReadService.unavailable().compare(),
        )

    def lending_positions(self):
        from holon_lending import LendingReadService
        return make_envelope(
            MessageKind.LENDING_POSITIONS,
            LendingReadService.unavailable().positions(None),
        )

    def lending_action_preview(self, payload):
        from holon_lending.preflight import unavailable_preview

        self.last_lending_action = payload
        return make_envelope(
            MessageKind.LENDING_ACTION_PREVIEW,
            unavailable_preview(
                "BASE_RPC_UNAVAILABLE", requested_action=payload["action"],
                amount_mode=payload["amount_mode"],
            ),
        )

    def lending_action_execute(self, payload, action_id):
        self.last_lending_execute = (payload, action_id)
        return make_envelope(
            MessageKind.PROTECTED_FLOW_STARTED,
            {
                "guard_state": "ACTIVE",
                "action_state": "AWAITING_LOCAL_CONFIRMATION",
                "flow_id": "11111111-1111-4111-8111-111111111111",
                "code": "AWAITING_LOCAL_CONFIRMATION",
                "message": "Action status is available.",
            },
            action_id=action_id,
        )

    def prepare_transfer(self, payload, action_id):
        self.last_transfer = (payload, action_id)
        return make_envelope(
            MessageKind.PROTECTED_FLOW_STARTED,
            {
                "guard_state": "ACTIVE",
                "action_state": "AWAITING_LOCAL_CONFIRMATION",
                "flow_id": "11111111-1111-4111-8111-111111111111",
                "code": "AWAITING_LOCAL_CONFIRMATION",
                "message": "Action status is available.",
            },
            action_id=action_id,
        )

    def transfer_status(self, action_id):
        return self._action_status(action_id, self.action_state)

    def cancel_transfer(self, action_id):
        return self._action_status(action_id, "REJECTED")

    def recover_transfer(self, action_id):
        return make_envelope(
            MessageKind.ACTION_STATUS,
            {
                "guard_state": "NORMAL",
                "action_state": "RECOVERY_REQUIRED",
                "flow_id": None,
                "code": "RECOVERY_COMPLETED",
                "message": "Action status is available.",
            },
            action_id=action_id,
        )

    def _action_status(self, action_id, state):
        return make_envelope(
            MessageKind.ACTION_STATUS,
            {
                "guard_state": (
                    "ACTIVE" if state == "AWAITING_LOCAL_CONFIRMATION" else "NORMAL"
                ),
                "action_state": state,
                "flow_id": None,
                "code": "ACTION_STATUS",
                "message": "Action status is available.",
            },
            action_id=action_id,
        )


class RaisingConnector:
    def probe(self) -> GuardHealth:
        raise RuntimeError("sensitive traceback detail")

    def ensure_available(self) -> GuardHealth:
        raise RuntimeError("sensitive traceback detail")


class FakeContext:
    def __init__(self) -> None:
        self.tools: list[dict] = []
        self.hooks: list[tuple[str, object]] = []

    def register_tool(self, **kwargs: object) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name: str, callback: object) -> None:
        self.hooks.append((name, callback))


class PluginTests(unittest.TestCase):
    def test_registers_authority_tools_and_two_hooks(self) -> None:
        context = FakeContext()
        plugin.register(context)
        self.assertEqual(
            [tool["name"] for tool in context.tools],
            [
                "holon_health", "holon_open_wallet", "holon_wallet_balances",
                "holon_lending_compare", "holon_lending_positions",
                "holon_lending_prepare",
                "holon_lending_execute",
                "holon_prepare_transfer", "holon_transfer_status",
                "holon_cancel_transfer", "holon_recover_transfer",
                "holon_action_status", "holon_cancel_action", "holon_recover_action",
            ],
        )
        self.assertEqual([name for name, _ in context.hooks], ["on_session_start", "pre_tool_call"])

    def test_health_response_is_safe_and_authority_disabled(self) -> None:
        runtime = plugin.PluginRuntime(StaticConnector(GuardHealth.available(GuardState.NORMAL)))
        payload = json.loads(runtime.handle_health({"secret": "must-not-return"}))
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(
            payload["capabilities"], [
                "health", "open_wallet", "wallet_balances", "prepare_transfer",
                "transfer_status", "cancel_transfer", "recover_transfer",
                "lending_compare", "lending_positions",
                "lending_prepare", "lending_execute", "action_status",
                "cancel_action", "recover_action",
            ],
        )
        self.assertFalse(payload["authority_available"])
        self.assertNotIn("must-not-return", json.dumps(payload))
        self.assertNotIn("pid", runtime.handle_health().lower())

    def test_open_wallet_response_is_safe_and_does_not_echo_arguments(self) -> None:
        runtime = plugin.PluginRuntime(StaticConnector(GuardHealth.available(GuardState.NORMAL)))
        payload = json.loads(runtime.handle_open_wallet({"secret": "must-not-return"}))
        self.assertEqual(payload["status"], "ACTIVATED")
        self.assertFalse(payload["authority_available"])
        serialized = json.dumps(payload)
        self.assertNotIn("must-not-return", serialized)
        for field in ("pid", "path", "pipe", "launch_id"):
            self.assertNotIn(field, serialized.lower())

    def test_old_or_unavailable_guard_returns_wallet_unavailable(self) -> None:
        payload = json.loads(plugin.PluginRuntime(RaisingConnector()).handle_open_wallet())
        self.assertEqual(payload["status"], "DEGRADED")
        self.assertEqual(payload["code"], "WALLET_UNAVAILABLE")

    def test_balance_tool_returns_public_snapshot_without_echoing_arguments(self) -> None:
        runtime = plugin.PluginRuntime(StaticConnector(GuardHealth.available(GuardState.NORMAL)))
        payload = json.loads(runtime.handle_wallet_balances({"password": "hidden"}))
        self.assertEqual(payload["status"], "PARTIAL")
        self.assertEqual([item["network"] for item in payload["networks"]], ["ethereum", "base"])
        self.assertNotIn("hidden", json.dumps(payload))
        self.assertNotIn("password", json.dumps(payload).lower())

    def test_unavailable_balance_tool_keeps_two_networks_nonzero_ambiguous(self) -> None:
        payload = json.loads(plugin.PluginRuntime(RaisingConnector()).handle_wallet_balances())
        self.assertEqual(payload["status"], "DEGRADED")
        self.assertEqual(payload["code"], "WALLET_BALANCES_UNAVAILABLE")
        self.assertEqual(len(payload["networks"]), 2)
        self.assertTrue(all(item["balances"] is None for item in payload["networks"]))

    def test_lending_tools_return_safe_structured_unavailable(self) -> None:
        runtime = plugin.PluginRuntime(RaisingConnector())
        markets = json.loads(runtime.handle_lending_compare({"password": "hidden"}))
        positions = json.loads(runtime.handle_lending_positions({"password": "hidden"}))
        self.assertEqual(markets["code"], "LENDING_UNAVAILABLE")
        self.assertEqual(positions["code"], "LENDING_POSITIONS_UNAVAILABLE")
        self.assertEqual([item["protocol"] for item in markets["markets"]], [
            "aave-v3", "compound-v3", "morpho-v1",
        ])
        self.assertNotIn("hidden", json.dumps((markets, positions)))

    def test_health_exception_returns_generic_uncertain_response(self) -> None:
        payload = json.loads(plugin.PluginRuntime(RaisingConnector()).handle_health())
        self.assertEqual(payload["guard_status"], "UNCERTAIN")
        self.assertEqual(payload["code"], "GUARD_STATE_UNCERTAIN")
        self.assertNotIn("traceback", payload["message"].lower())

    def test_session_start_never_raises(self) -> None:
        plugin.PluginRuntime(RaisingConnector()).on_session_start(session_id="public")

    def test_every_non_lifecycle_tool_blocks_in_every_protected_state(self) -> None:
        for state in (
            GuardState.ENTERING,
            GuardState.ACTIVE,
            GuardState.EXITING,
            GuardState.RECOVERY_REQUIRED,
        ):
            with self.subTest(state=state):
                runtime = plugin.PluginRuntime(StaticConnector(GuardHealth.available(state)))
                for tool_name in (
                    "terminal", "browser", "future_unknown_tool",
                    "holon_open_wallet", "holon_wallet_balances",
                    "holon_lending_compare", "holon_lending_positions",
                    "holon_lending_prepare",
                    "holon_prepare_transfer",
                ):
                    with self.subTest(tool_name=tool_name):
                        self.assertEqual(
                            runtime.pre_tool_call(tool_name)["action"], "block",
                        )

    def test_lifecycle_allowlist_is_available_in_protected_state(self) -> None:
        runtime = plugin.PluginRuntime(
            StaticConnector(GuardHealth.available(GuardState.ACTIVE)),
        )
        for tool_name in (
            "holon_health", "holon_transfer_status", "holon_cancel_transfer",
            "holon_recover_transfer",
        ):
            with self.subTest(tool_name=tool_name):
                self.assertIsNone(runtime.pre_tool_call(tool_name))

    def test_normal_restores_all_tools(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.ACTIVE))
        runtime = plugin.PluginRuntime(connector)
        self.assertEqual(runtime.pre_tool_call("terminal")["action"], "block")
        connector.health = GuardHealth.available(GuardState.NORMAL)
        self.assertIsNone(runtime.pre_tool_call("terminal"))
        self.assertIsNone(runtime.pre_tool_call("future_unknown_tool"))

    def test_signing_disabled_does_not_globally_block_hermes(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.ACTIVE))
        runtime = plugin.PluginRuntime(connector)
        self.assertEqual(runtime.pre_tool_call("terminal")["action"], "block")
        connector.health = GuardHealth.available(GuardState.SIGNING_DISABLED)
        self.assertIsNone(runtime.pre_tool_call("terminal"))
        self.assertIsNone(runtime.pre_tool_call("future_unknown_tool"))

    def test_uncertain_state_blocks_only_after_protected_latch(self) -> None:
        connector = StaticConnector(GuardHealth.uncertain())
        runtime = plugin.PluginRuntime(connector)
        self.assertIsNone(runtime.pre_tool_call("terminal"))
        connector.health = GuardHealth.available(GuardState.ACTIVE)
        self.assertEqual(runtime.pre_tool_call("terminal")["action"], "block")
        connector.health = GuardHealth.uncertain()
        self.assertEqual(runtime.pre_tool_call("terminal")["action"], "block")

    def test_callback_exception_uses_existing_latch(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.ACTIVE))
        runtime = plugin.PluginRuntime(connector)
        runtime.pre_tool_call("terminal")
        with patch.object(connector, "probe", side_effect=RuntimeError("detail")):
            result = runtime.pre_tool_call("terminal", args={"secret": "hidden"})
        self.assertEqual(result["action"], "block")
        self.assertNotIn("detail", result["message"])
        self.assertNotIn("hidden", result["message"])

    def test_prepare_transfer_generates_action_and_returns_safe_intent(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.NORMAL))
        runtime = plugin.PluginRuntime(connector)
        result = json.loads(runtime.handle_prepare_transfer({
            "network": "base", "asset": "usdc", "amount": "1,25",
            "recipient": "0x1111111111111111111111111111111111111111",
        }))
        self.assertEqual(result["status"], "AWAITING_LOCAL_CONFIRMATION")
        self.assertTrue(result["action_id"].startswith("act-"))
        self.assertEqual(connector.last_transfer[1], result["action_id"])
        serialized = json.dumps(result).lower()
        for field in ("flow_id", "digest", "pid", "path", "pipe", "password"):
            self.assertNotIn(field, serialized)
        self.assertEqual(result["turn_state"], "END_REQUIRED")
        self.assertIn("End this turn", result["next_step"])

    def test_successful_prepare_latches_and_blocks_second_prepare(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.NORMAL))
        runtime = plugin.PluginRuntime(connector)
        runtime.handle_prepare_transfer({
            "network": "base", "asset": "eth", "amount": "0.01",
            "recipient": "0x1111111111111111111111111111111111111111",
        })
        connector.health = GuardHealth.available(GuardState.ACTIVE)
        result = runtime.pre_tool_call("holon_prepare_transfer")
        self.assertEqual(result["action"], "block")

    def test_lending_prepare_is_non_authoritative_and_injects_fixed_identity(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.SIGNING_DISABLED))
        runtime = plugin.PluginRuntime(connector)
        result = json.loads(runtime.handle_lending_prepare({
            "action": "withdraw", "amount_mode": "all", "amount": None,
        }))
        self.assertEqual(result["status"], "UNAVAILABLE")
        self.assertFalse(result["authority_available"])
        self.assertFalse(result["execution_available"])
        self.assertEqual(connector.last_lending_action, {
            "module_id": "lending", "module_version": "1",
            "protocol_profile_id": "aave-v3-base-usdc",
            "protocol_profile_version": "1", "network": "base", "asset": "usdc",
            "beneficiary_mode": "active_wallet_account", "action": "withdraw",
            "amount_mode": "all", "amount": None,
        })
        serialized = json.dumps(result).lower()
        for forbidden in ("password", "private_key", "action_id"):
            self.assertNotIn(forbidden, serialized)
        self.assertNotIn('"calldata":', serialized)

    def test_lending_prepare_accepts_supply_all_and_calls_guard(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.NORMAL))
        runtime = plugin.PluginRuntime(connector)
        result = json.loads(runtime.handle_lending_prepare({
            "action": "supply", "amount_mode": "all", "amount": None,
        }))
        self.assertEqual(result["code"], "LENDING_ACTION_UNAVAILABLE")
        self.assertEqual(connector.last_lending_action["action"], "supply")
        self.assertIsNone(connector.last_lending_action["amount"])

    def test_lending_execute_allows_supply_and_withdraw_exact_or_all(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.NORMAL))
        runtime = plugin.PluginRuntime(connector)
        result = json.loads(runtime.handle_lending_execute({
            "action": "withdraw", "amount_mode": "all", "amount": None,
        }))
        self.assertEqual(result["status"], "AWAITING_LOCAL_CONFIRMATION")
        self.assertEqual(result["action"], "withdraw")
        self.assertEqual(result["amount_mode"], "all")
        intent, action_id = connector.last_lending_execute
        self.assertEqual(intent["beneficiary_mode"], "active_wallet_account")
        self.assertEqual(intent["amount"], None)
        self.assertEqual(result["action_id"], action_id)
        self.assertEqual(result["turn_state"], "END_REQUIRED")

        supply = json.loads(plugin.PluginRuntime(connector).handle_lending_execute({
            "action": "supply", "amount_mode": "exact", "amount": "1",
        }))
        self.assertEqual(supply["status"], "AWAITING_LOCAL_CONFIRMATION")
        self.assertEqual(supply["action"], "supply")

    def test_failed_lending_status_offers_only_confirmed_fresh_retry(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.NORMAL))
        runtime = plugin.PluginRuntime(connector)
        prepared = json.loads(runtime.handle_lending_execute({
            "protocol": "morpho-v1", "action": "withdraw",
            "amount_mode": "all", "amount": None,
        }))
        connector.action_state = "FAILED"
        failed = json.loads(runtime.handle_transfer_status({
            "action_id": prepared["action_id"],
        }))

        self.assertEqual(failed["status"], "FAILED")
        self.assertTrue(failed["retry"]["available"])
        self.assertFalse(failed["retry"]["automatic"])
        self.assertTrue(failed["retry"]["requires_user_confirmation"])
        self.assertTrue(failed["retry"]["fresh_preflight"])
        self.assertTrue(failed["retry"]["new_action_id"])
        self.assertEqual(failed["retry"]["request"], {
            "protocol": "morpho-v1", "action": "withdraw",
            "amount_mode": "all", "amount": None,
        })
        self.assertIn("explicit confirmation", failed["next_step"])

    def test_status_and_cancel_expose_no_internal_flow(self) -> None:
        runtime = plugin.PluginRuntime(
            StaticConnector(GuardHealth.available(GuardState.ACTIVE)),
        )
        action_id = "act-22222222-2222-4222-8222-222222222222"
        status = json.loads(runtime.handle_transfer_status({"action_id": action_id}))
        cancelled = json.loads(runtime.handle_cancel_transfer({"action_id": action_id}))
        self.assertEqual(status["status"], "AWAITING_LOCAL_CONFIRMATION")
        self.assertEqual(cancelled["status"], "REJECTED")
        self.assertNotIn("flow", json.dumps((status, cancelled)).lower())

    def test_mismatched_terminal_response_cannot_clear_retained_latch(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.NORMAL))
        runtime = plugin.PluginRuntime(connector)
        prepared = json.loads(runtime.handle_prepare_transfer({
            "network": "base", "asset": "eth", "amount": "0.01",
            "recipient": "0x1111111111111111111111111111111111111111",
        }))
        connector.health = GuardHealth.uncertain()
        other_action = "act-33333333-3333-4333-8333-333333333333"
        runtime.handle_cancel_transfer({"action_id": other_action})
        self.assertEqual(runtime.pre_tool_call("terminal")["action"], "block")
        runtime.handle_cancel_transfer({"action_id": prepared["action_id"]})
        self.assertIsNone(runtime.pre_tool_call("terminal"))

    def test_recovery_returns_safe_terminal_result_and_clears_exact_latch(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.RECOVERY_REQUIRED))
        runtime = plugin.PluginRuntime(connector)
        runtime.on_session_start()
        action_id = "act-22222222-2222-4222-8222-222222222222"
        payload = json.loads(runtime.handle_recover_transfer({"action_id": action_id}))
        self.assertEqual(payload["status"], "RECOVERED")
        self.assertEqual(payload["code"], "RECOVERY_COMPLETED")
        self.assertNotIn("flow", json.dumps(payload).lower())
        connector.health = GuardHealth.available(GuardState.NORMAL)
        self.assertIsNone(runtime.pre_tool_call("terminal"))

    def test_invalid_recovery_arguments_do_not_echo_secret_canary(self) -> None:
        runtime = plugin.PluginRuntime(RaisingConnector())
        payload = runtime.handle_recover_transfer({
            "action_id": "bad", "password": "hidden-canary",
        })
        self.assertNotIn("hidden-canary", payload)
        self.assertNotIn("password", payload.lower())


if __name__ == "__main__":
    unittest.main()
