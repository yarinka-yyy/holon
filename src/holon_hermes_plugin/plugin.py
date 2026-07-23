"""Safe Hermes-facing capability and protected-turn hook for M2.01."""

from __future__ import annotations

import json
from typing import Any, Optional

from .guard import (
    PROTECTED_STATES,
    GuardAvailability,
    GuardConnector,
    GuardHealth,
    GuardState,
    PipeGuardClient,
    production_launcher,
)

HEALTH_TOOL = "holon_health"
OPEN_WALLET_TOOL = "holon_open_wallet"
PILOT_BLOCKED_TOOL = "terminal"


class PluginRuntime:
    def __init__(self, connector: GuardConnector) -> None:
        self._connector = connector
        self._protected_latch = False

    def _observe(self, health: GuardHealth) -> None:
        if health.availability is not GuardAvailability.AVAILABLE:
            return
        if health.state in PROTECTED_STATES:
            self._protected_latch = True
        elif health.state is GuardState.NORMAL:
            self._protected_latch = False

    def _health_response(self, health: GuardHealth) -> str:
        status = "READY" if health.availability is GuardAvailability.AVAILABLE else "DEGRADED"
        return json.dumps(
            {
                "status": status,
                "capabilities": ["health", "open_wallet"],
                "authority_available": False,
                "guard_status": health.availability.value,
                "guard_state": health.state.value,
                "code": health.code,
                "message": health.message,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def handle_health(self, params: Optional[dict] = None, **kwargs: Any) -> str:
        del params, kwargs
        try:
            health = self._connector.probe()
            self._observe(health)
            return self._health_response(health)
        except Exception:
            return self._health_response(GuardHealth.uncertain())

    def handle_open_wallet(self, params: Optional[dict] = None, **kwargs: Any) -> str:
        del params, kwargs
        try:
            response = self._connector.open_wallet()
            payload = response.payload
            if response.kind.value == "wallet_opened":
                return json.dumps(
                    {
                        "status": payload["wallet_state"],
                        "capabilities": ["health", "open_wallet"],
                        "authority_available": False,
                        "code": payload["code"],
                        "message": payload["message"],
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            code = "WALLET_UNAVAILABLE"
            message = "Wallet could not be opened."
        except Exception:
            code = "WALLET_UNAVAILABLE"
            message = "Wallet could not be opened."
        return json.dumps(
            {
                "status": "DEGRADED",
                "capabilities": ["health", "open_wallet"],
                "authority_available": False,
                "code": code,
                "message": message,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def on_session_start(self, **kwargs: Any) -> None:
        del kwargs
        try:
            self._observe(self._connector.ensure_available())
        except Exception:
            return

    def pre_tool_call(
        self,
        tool_name: str = "",
        args: Any = None,
        **kwargs: Any,
    ) -> Optional[dict[str, str]]:
        del args, kwargs
        if tool_name == HEALTH_TOOL:
            return None
        if tool_name != PILOT_BLOCKED_TOOL:
            return None
        try:
            health = self._connector.probe()
            self._observe(health)
            should_block = health.state in PROTECTED_STATES or self._protected_latch
        except Exception:
            should_block = self._protected_latch
        if not should_block:
            return None
        return {
            "action": "block",
            "message": "[Holon] The terminal is blocked while a protected Wallet flow may be active.",
        }


_runtime = PluginRuntime(GuardConnector(PipeGuardClient(), production_launcher()))


def _handle_health(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_health(params, **kwargs)


def _handle_open_wallet(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_open_wallet(params, **kwargs)


def _on_session_start(**kwargs: Any) -> None:
    _runtime.on_session_start(**kwargs)


def _on_pre_tool_call(**kwargs: Any) -> Optional[dict[str, str]]:
    return _runtime.pre_tool_call(**kwargs)


def register(ctx: Any) -> None:
    ctx.register_tool(
        name=HEALTH_TOOL,
        toolset="holon",
        schema={
            "name": HEALTH_TOOL,
            "description": "Return safe Holon and Guard health status.",
            "parameters": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
        },
        handler=_handle_health,
        description="Return safe Holon health status.",
    )
    ctx.register_tool(
        name=OPEN_WALLET_TOOL,
        toolset="holon",
        schema={
            "name": OPEN_WALLET_TOOL,
            "description": "Open or activate the local Holon Wallet.",
            "parameters": {
                "type": "object", "properties": {}, "required": [],
                "additionalProperties": False,
            },
        },
        handler=_handle_open_wallet,
        description="Open or activate the local Holon Wallet.",
    )
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
