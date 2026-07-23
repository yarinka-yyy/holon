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
    def test_registers_health_and_open_wallet_tools_and_two_hooks(self) -> None:
        context = FakeContext()
        plugin.register(context)
        self.assertEqual(
            [tool["name"] for tool in context.tools],
            ["holon_health", "holon_open_wallet"],
        )
        self.assertEqual([name for name, _ in context.hooks], ["on_session_start", "pre_tool_call"])

    def test_health_response_is_safe_and_authority_disabled(self) -> None:
        runtime = plugin.PluginRuntime(StaticConnector(GuardHealth.available(GuardState.NORMAL)))
        payload = json.loads(runtime.handle_health({"secret": "must-not-return"}))
        self.assertEqual(payload["status"], "READY")
        self.assertEqual(payload["capabilities"], ["health", "open_wallet"])
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

    def test_health_exception_returns_generic_uncertain_response(self) -> None:
        payload = json.loads(plugin.PluginRuntime(RaisingConnector()).handle_health())
        self.assertEqual(payload["guard_status"], "UNCERTAIN")
        self.assertEqual(payload["code"], "GUARD_STATE_UNCERTAIN")
        self.assertNotIn("traceback", payload["message"].lower())

    def test_session_start_never_raises(self) -> None:
        plugin.PluginRuntime(RaisingConnector()).on_session_start(session_id="public")

    def test_terminal_blocks_in_every_protected_state(self) -> None:
        for state in (
            GuardState.ENTERING,
            GuardState.ACTIVE,
            GuardState.EXITING,
            GuardState.RECOVERY_REQUIRED,
        ):
            with self.subTest(state=state):
                runtime = plugin.PluginRuntime(StaticConnector(GuardHealth.available(state)))
                self.assertEqual(runtime.pre_tool_call("terminal")["action"], "block")

    def test_health_is_allowed_and_normal_restores_terminal(self) -> None:
        connector = StaticConnector(GuardHealth.available(GuardState.ACTIVE))
        runtime = plugin.PluginRuntime(connector)
        self.assertIsNone(runtime.pre_tool_call("holon_health"))
        self.assertEqual(runtime.pre_tool_call("terminal")["action"], "block")
        connector.health = GuardHealth.available(GuardState.NORMAL)
        self.assertIsNone(runtime.pre_tool_call("terminal"))

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


if __name__ == "__main__":
    unittest.main()
