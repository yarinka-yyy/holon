"""Safe Hermes-facing capability and protected-turn hook for M2.01."""

from __future__ import annotations

import json
from typing import Any, Optional

from holon_contracts import MessageKind, new_action_id

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
WALLET_BALANCES_TOOL = "holon_wallet_balances"
PREPARE_TRANSFER_TOOL = "holon_prepare_transfer"
TRANSFER_STATUS_TOOL = "holon_transfer_status"
CANCEL_TRANSFER_TOOL = "holon_cancel_transfer"
PILOT_BLOCKED_TOOL = "terminal"
CAPABILITIES = [
    "health", "open_wallet", "wallet_balances", "prepare_transfer",
    "transfer_status", "cancel_transfer",
]


def _unavailable_balances() -> dict[str, Any]:
    networks = []
    for network, chain_id in (("ethereum", 1), ("base", 8453)):
        networks.append(
            {
                "network": network,
                "chain_id": chain_id,
                "status": "UNAVAILABLE",
                "block_number": None,
                "updated_at": None,
                "error_code": "WALLET_BALANCES_UNAVAILABLE",
                "balances": None,
            }
        )
    return {
        "status": "DEGRADED",
        "authority_available": False,
        "account": None,
        "networks": networks,
        "code": "WALLET_BALANCES_UNAVAILABLE",
        "message": "Wallet balances are unavailable.",
    }


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
                "capabilities": CAPABILITIES,
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
                        "capabilities": CAPABILITIES,
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
                "capabilities": CAPABILITIES,
                "authority_available": False,
                "code": code,
                "message": message,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def handle_wallet_balances(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del params, kwargs
        try:
            response = self._connector.wallet_balances()
            if response.kind.value == "wallet_balances":
                return json.dumps(
                    response.payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
        except Exception:
            pass
        return json.dumps(
            _unavailable_balances(),
            ensure_ascii=False,
            separators=(",", ":"),
        )

    @staticmethod
    def _safe_transfer_failure(action_id: str | None = None) -> str:
        payload: dict[str, Any] = {
            "status": "DEGRADED",
            "authority_available": False,
            "code": "WALLET_TRANSFER_UNAVAILABLE",
            "message": "Wallet transfer preparation is unavailable.",
        }
        if action_id is not None:
            payload["action_id"] = action_id
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))

    def handle_prepare_transfer(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del kwargs
        if not isinstance(params, dict) or set(params) != {
            "network", "asset", "amount", "recipient",
        }:
            return self._safe_transfer_failure()
        action_id = new_action_id()
        try:
            response = self._connector.prepare_transfer(dict(params), action_id)
        except Exception:
            return self._safe_transfer_failure(action_id)
        if response.kind is MessageKind.PROTECTED_FLOW_STARTED:
            self._protected_latch = True
            return json.dumps(
                {
                    "status": "AWAITING_LOCAL_CONFIRMATION",
                    "authority_available": True,
                    "action_id": action_id,
                    "network": params["network"],
                    "asset": params["asset"],
                    "amount": params["amount"],
                    "recipient": params["recipient"],
                    "code": response.payload["code"],
                    "message": "Review and confirm the exact transfer in Wallet.",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return json.dumps(
            {
                "status": "REFUSED",
                "authority_available": False,
                "action_id": action_id,
                "code": response.payload.get("code", "TRANSFER_REFUSED"),
                "message": response.payload.get("message", "Transfer was refused."),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _handle_transfer_action(
        self, params: Optional[dict], *, cancel: bool,
    ) -> str:
        if not isinstance(params, dict) or set(params) != {"action_id"}:
            return self._safe_transfer_failure()
        action_id = params.get("action_id")
        if not isinstance(action_id, str):
            return self._safe_transfer_failure()
        try:
            response = (
                self._connector.cancel_transfer(action_id)
                if cancel else self._connector.transfer_status(action_id)
            )
        except Exception:
            return self._safe_transfer_failure()
        payload = response.payload
        if response.kind is MessageKind.ACTION_STATUS:
            state = payload["action_state"]
            if state in {"REJECTED", "COMPLETED", "FAILED", "REFUSED"}:
                self._protected_latch = False
            return json.dumps(
                {
                    "status": state,
                    "authority_available": state == "AWAITING_LOCAL_CONFIRMATION",
                    "action_id": action_id,
                    "code": payload["code"],
                    "message": payload["message"],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return json.dumps(
            {
                "status": "REFUSED",
                "authority_available": False,
                "action_id": action_id,
                "code": payload.get("code", "ACTION_UNAVAILABLE"),
                "message": payload.get("message", "Action is unavailable."),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def handle_transfer_status(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del kwargs
        return self._handle_transfer_action(params, cancel=False)

    def handle_cancel_transfer(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del kwargs
        return self._handle_transfer_action(params, cancel=True)

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


def _handle_wallet_balances(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_wallet_balances(params, **kwargs)


def _handle_prepare_transfer(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_prepare_transfer(params, **kwargs)


def _handle_transfer_status(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_transfer_status(params, **kwargs)


def _handle_cancel_transfer(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_cancel_transfer(params, **kwargs)


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
    ctx.register_tool(
        name=WALLET_BALANCES_TOOL,
        toolset="holon",
        schema={
            "name": WALLET_BALANCES_TOOL,
            "description": (
                "Read live public ETH and USDC balances for the active Holon "
                "Account on Ethereum and Base. Use when a request depends on "
                "available Wallet funds."
            ),
            "parameters": {
                "type": "object", "properties": {}, "required": [],
                "additionalProperties": False,
            },
        },
        handler=_handle_wallet_balances,
        description="Read live public balances for the active Holon Account.",
    )
    transfer_properties = {
        "network": {"type": "string", "enum": ["ethereum", "base"]},
        "asset": {"type": "string", "enum": ["eth", "usdc"]},
        "amount": {"type": "string"},
        "recipient": {"type": "string"},
    }
    ctx.register_tool(
        name=PREPARE_TRANSFER_TOOL,
        toolset="holon",
        schema={
            "name": PREPARE_TRANSFER_TOOL,
            "description": "Prepare an exact ETH or USDC transfer for local Wallet review.",
            "parameters": {
                "type": "object", "properties": transfer_properties,
                "required": ["network", "asset", "amount", "recipient"],
                "additionalProperties": False,
            },
        },
        handler=_handle_prepare_transfer,
        description="Prepare an exact transfer for local Wallet confirmation.",
    )
    action_parameters = {
        "type": "object",
        "properties": {"action_id": {"type": "string"}},
        "required": ["action_id"],
        "additionalProperties": False,
    }
    ctx.register_tool(
        name=TRANSFER_STATUS_TOOL,
        toolset="holon",
        schema={
            "name": TRANSFER_STATUS_TOOL,
            "description": "Read the public lifecycle status of a prepared transfer.",
            "parameters": action_parameters,
        },
        handler=_handle_transfer_status,
        description="Read prepared transfer status.",
    )
    ctx.register_tool(
        name=CANCEL_TRANSFER_TOOL,
        toolset="holon",
        schema={
            "name": CANCEL_TRANSFER_TOOL,
            "description": "Cancel a prepared transfer before submission.",
            "parameters": action_parameters,
        },
        handler=_handle_cancel_transfer,
        description="Cancel a prepared transfer.",
    )
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
