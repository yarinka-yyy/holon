"""Safe Hermes-facing capability and protected-turn hook for M2.01."""

from __future__ import annotations

import json
from collections import OrderedDict
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
LENDING_COMPARE_TOOL = "holon_lending_compare"
LENDING_POSITIONS_TOOL = "holon_lending_positions"
LENDING_PORTFOLIO_TOOL = "holon_lending_portfolio"
LENDING_PREPARE_TOOL = "holon_lending_prepare"
LENDING_EXECUTE_TOOL = "holon_lending_execute"
PREPARE_TRANSFER_TOOL = "holon_prepare_transfer"
TRANSFER_STATUS_TOOL = "holon_transfer_status"
CANCEL_TRANSFER_TOOL = "holon_cancel_transfer"
RECOVER_TRANSFER_TOOL = "holon_recover_transfer"
ACTION_STATUS_TOOL = "holon_action_status"
CANCEL_ACTION_TOOL = "holon_cancel_action"
RECOVER_ACTION_TOOL = "holon_recover_action"
CAPABILITIES = [
    "health", "open_wallet", "wallet_balances", "prepare_transfer",
    "transfer_status", "cancel_transfer", "recover_transfer", "lending_compare",
    "lending_positions", "lending_portfolio", "lending_prepare", "lending_execute",
    "action_status", "cancel_action", "recover_action",
]
PROTECTED_TOOL_ALLOWLIST = frozenset({
    HEALTH_TOOL, TRANSFER_STATUS_TOOL, CANCEL_TRANSFER_TOOL, RECOVER_TRANSFER_TOOL,
    ACTION_STATUS_TOOL, CANCEL_ACTION_TOOL, RECOVER_ACTION_TOOL,
})


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


LENDING_IDENTITIES = (
    ("aave-v3", "base-usdc", "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"),
    ("compound-v3", "base-usdc", "0xb125E6687d4313864e53df431d5425969c15Eb2F"),
    ("morpho-v1", "gauntlet-usdc-prime-v1", "0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61"),
)
LENDING_WRITE_PROFILES = {
    "aave-v3": "aave-v3-base-usdc",
    "compound-v3": "compound-v3-base-usdc",
    "morpho-v1": "morpho-v1-gauntlet-usdc-prime",
}


def _lending_root() -> dict[str, Any]:
    return {
        "status": "DEGRADED", "authority_available": False,
        "network": {"network": "base", "chain_id": 8453},
        "asset": {
            "asset": "USDC", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "decimals": 6,
        },
    }


def _unavailable_lending_markets() -> dict[str, Any]:
    value = _lending_root()
    value.update({
        "markets": [{
            "protocol": protocol, "market_id": market,
            "contract_address": contract, "base_yield": None,
            "incentives": {
                "status": "UNAVAILABLE", "total_apr_percent": None,
                "components": [],
            },
            "confirmed_total_annual_percent": None,
            "total_completeness": "UNAVAILABLE",
            "freshness": {
                "state": "UNAVAILABLE", "observed_at": None, "block_number": None,
            },
            "caveats": ["READ_PROFILES_UNAVAILABLE"],
        } for protocol, market, contract in LENDING_IDENTITIES],
        "highest_observed": None, "recommendation": None,
        "delivery": {
            "fetched_at": "2026-01-01T00:00:00Z", "cache_age_seconds": 0,
            "cache_max_age_seconds": 30, "force_refreshed": False,
        },
        "code": "LENDING_UNAVAILABLE",
        "message": "Lending data is unavailable.",
    })
    return value


def _unavailable_lending_preview() -> dict[str, Any]:
    return {
        "status": "UNAVAILABLE", "authority_available": False,
        "execution_available": False, "account": None,
        "requested_action": None, "next_action": None,
        "protocol": "aave-v3", "profile_id": "aave-v3-base-usdc",
        "profile_version": "1", "profile_digest": None,
        "network": {"network": "base", "chain_id": 8453},
        "asset": {
            "asset": "USDC", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
            "decimals": 6,
        },
        "amount_mode": None, "amount_atomic": None, "call_amount_atomic": None,
        "display_amount": None,
        "target": None, "method": None, "calldata_hash": None,
        "native_value_wei": "0", "nonce": None, "gas": None,
        "max_fee_per_gas_wei": None, "max_priority_fee_per_gas_wei": None,
        "l2_fee_ceiling_wei": None, "l1_fee_upper_bound_wei": None,
        "max_total_fee_wei": None, "block_number": None, "observed_at": None,
        "expires_at": None, "preview_digest": None, "checks": [],
        "caveats": ["WALLET_UNAVAILABLE"], "code": "LENDING_ACTION_UNAVAILABLE",
        "message": "Lending action preview is unavailable.",
    }


def _unavailable_lending_positions() -> dict[str, Any]:
    value = _lending_root()
    value.update({
        "account": None,
        "positions": [{
            "protocol": protocol, "market_id": market,
            "contract_address": contract, "amount_atomic": None, "decimals": 6,
            "display_amount": None,
            "freshness": {
                "state": "UNAVAILABLE", "observed_at": None, "block_number": None,
            },
            "caveats": ["READ_PROFILES_UNAVAILABLE"],
        } for protocol, market, contract in LENDING_IDENTITIES],
        "code": "LENDING_POSITIONS_UNAVAILABLE",
        "message": "Lending positions are unavailable.",
    })
    return value


def _unavailable_lending_portfolio(history_period: str = "none") -> dict[str, Any]:
    value = _lending_root()
    value.update({
        "account": None,
        "summary": {
            "total_position_atomic": None, "display_total_position": None,
            "tracked_earnings_atomic": None, "display_tracked_earnings": None,
            "earnings_status": "NOT_ENOUGH_HISTORY",
            "weighted_confirmed_annual_percent": None,
            "yield_completeness": "PARTIAL",
        },
        "protocols": [{
            "protocol": protocol, "market_id": market,
            "display_name": {
                "aave-v3": "Aave V3", "compound-v3": "Compound III",
                "morpho-v1": "Morpho Gauntlet USDC Prime",
            }[protocol],
            "contract_address": contract, "position_atomic": None,
            "display_position": None, "base_yield": None, "incentives": None,
            "confirmed_total_annual_percent": None,
            "total_completeness": "UNAVAILABLE",
            "tracked_earnings_atomic": None,
            "display_tracked_earnings": None,
            "earnings_status": "NOT_ENOUGH_HISTORY", "tracked_since": None,
            "data_state": "UNAVAILABLE", "observed_at": None,
            "caveats": ["LENDING_PORTFOLIO_UNAVAILABLE"],
        } for protocol, market, contract in LENDING_IDENTITIES],
        "recommendation": None,
        "delivery": {
            "fetched_at": None, "cache_age_seconds": 0,
            "cache_max_age_seconds": 30, "force_refreshed": False,
            "source": "UNAVAILABLE",
        },
        "history": {"period": history_period, "points": []},
        "code": "LENDING_PORTFOLIO_UNAVAILABLE",
        "message": "Lending portfolio is unavailable.",
    })
    return value


class PluginRuntime:
    def __init__(self, connector: GuardConnector) -> None:
        self._connector = connector
        self._protected_latch = False
        self._protected_action_id: str | None = None
        self._lending_requests: OrderedDict[str, dict[str, object]] = OrderedDict()

    def _remember_lending_request(
        self, action_id: str, params: dict[str, object],
    ) -> None:
        self._lending_requests[action_id] = {
            "protocol": params["protocol"], "action": params["action"],
            "amount_mode": params["amount_mode"], "amount": params["amount"],
        }
        self._lending_requests.move_to_end(action_id)
        while len(self._lending_requests) > 32:
            self._lending_requests.popitem(last=False)

    def _observe(self, health: GuardHealth) -> None:
        if health.availability is not GuardAvailability.AVAILABLE:
            return
        if health.state in PROTECTED_STATES:
            self._protected_latch = True
        elif health.state in {GuardState.NORMAL, GuardState.SIGNING_DISABLED}:
            self._protected_latch = False
            self._protected_action_id = None

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

    def handle_lending_compare(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del kwargs
        values = {} if params is None else params
        if not isinstance(values, dict) or set(values) - {"force_refresh"}:
            return json.dumps(_unavailable_lending_markets(), separators=(",", ":"))
        force_refresh = values.get("force_refresh", False)
        if type(force_refresh) is not bool:
            return json.dumps(_unavailable_lending_markets(), separators=(",", ":"))
        try:
            response = self._connector.lending_markets(force_refresh)
            if response.kind is MessageKind.LENDING_MARKETS:
                return json.dumps(response.payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass
        return json.dumps(
            _unavailable_lending_markets(), ensure_ascii=False, separators=(",", ":"),
        )

    def handle_lending_positions(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del params, kwargs
        try:
            response = self._connector.lending_positions()
            if response.kind is MessageKind.LENDING_POSITIONS:
                return json.dumps(response.payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass
        return json.dumps(
            _unavailable_lending_positions(), ensure_ascii=False, separators=(",", ":"),
        )

    def handle_lending_portfolio(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del kwargs
        values = {} if params is None else params
        if not isinstance(values, dict) or set(values) - {"force_refresh", "history_period"}:
            return json.dumps(_unavailable_lending_portfolio(), separators=(",", ":"))
        force_refresh = values.get("force_refresh", False)
        history_period = values.get("history_period", "none")
        if type(force_refresh) is not bool or history_period not in {"none", "7d", "30d", "all"}:
            return json.dumps(_unavailable_lending_portfolio(), separators=(",", ":"))
        try:
            response = self._connector.lending_portfolio(force_refresh, history_period)
            if response.kind is MessageKind.LENDING_PORTFOLIO:
                return json.dumps(response.payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass
        return json.dumps(
            _unavailable_lending_portfolio(history_period),
            ensure_ascii=False, separators=(",", ":"),
        )

    def handle_lending_prepare(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del kwargs
        if not isinstance(params, dict) or set(params) not in (
            {"action", "amount_mode", "amount"},
            {"protocol", "action", "amount_mode", "amount"},
        ):
            return json.dumps(_unavailable_lending_preview(), separators=(",", ":"))
        protocol = params.get("protocol", "aave-v3")
        action, mode, amount = params.get("action"), params.get("amount_mode"), params.get("amount")
        if (
            protocol not in LENDING_WRITE_PROFILES
            or action not in {"supply", "withdraw"} or mode not in {"exact", "all"}
            or mode == "all" and amount is not None
            or mode == "exact" and not isinstance(amount, str)
        ):
            return json.dumps(_unavailable_lending_preview(), separators=(",", ":"))
        intent = {
            "module_id": "lending", "module_version": "1",
            "protocol_profile_id": LENDING_WRITE_PROFILES[protocol],
            "protocol_profile_version": "1", "network": "base", "asset": "usdc",
            "beneficiary_mode": "active_wallet_account", "action": action,
            "amount_mode": mode, "amount": amount,
        }
        try:
            response = self._connector.lending_action_preview(intent)
            if response.kind is MessageKind.LENDING_ACTION_PREVIEW:
                return json.dumps(response.payload, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            pass
        return json.dumps(_unavailable_lending_preview(), separators=(",", ":"))

    def handle_lending_execute(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del kwargs
        if not isinstance(params, dict) or set(params) not in (
            {"action", "amount_mode", "amount"},
            {"protocol", "action", "amount_mode", "amount"},
        ):
            return self._safe_transfer_failure()
        protocol = params.get("protocol", "aave-v3")
        if (
            protocol not in LENDING_WRITE_PROFILES
            or params.get("action") not in {"supply", "withdraw"}
            or params.get("amount_mode") not in {"exact", "all"}
            or (
                params.get("amount_mode") == "exact"
                and not isinstance(params.get("amount"), str)
            )
            or (
                params.get("amount_mode") == "all" and params.get("amount") is not None
            )
        ):
            return self._safe_transfer_failure()
        action_id = new_action_id()
        intent = {
            "module_id": "lending", "module_version": "1",
            "protocol_profile_id": LENDING_WRITE_PROFILES[protocol],
            "protocol_profile_version": "1", "network": "base", "asset": "usdc",
            "beneficiary_mode": "active_wallet_account",
            "action": params["action"], "amount_mode": params["amount_mode"],
            "amount": params["amount"],
        }
        try:
            response = self._connector.lending_action_execute(intent, action_id)
        except Exception:
            return self._safe_transfer_failure(action_id)
        if response.kind is MessageKind.PROTECTED_FLOW_STARTED:
            self._protected_latch = True
            self._protected_action_id = action_id
            self._remember_lending_request(action_id, {
                "protocol": protocol, "action": params["action"],
                "amount_mode": params["amount_mode"], "amount": params["amount"],
            })
            return json.dumps({
                "status": "AWAITING_LOCAL_CONFIRMATION", "authority_available": True,
                "action_id": action_id, "protocol": protocol, "action": params["action"],
                "amount_mode": params["amount_mode"], "amount": params["amount"],
                "code": response.payload["code"],
                "message": f"Review and confirm the independent {protocol} action in Wallet.",
                "turn_state": "END_REQUIRED",
                "next_step": (
                    "End this turn and wait for the user's Wallet decision. When the user "
                    "returns, call holon_action_status with this action_id."
                ),
            }, separators=(",", ":"))
        return json.dumps({
            "status": "REFUSED", "authority_available": False, "action_id": action_id,
            "code": response.payload.get("code", "LENDING_ACTION_REFUSED"),
            "message": response.payload.get("message", "Lending action was refused."),
        }, separators=(",", ":"))

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
            self._protected_action_id = action_id
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
                    "turn_state": "END_REQUIRED",
                    "next_step": "End this turn and wait for the user's decision in Wallet.",
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
            self._observe_action_payload(payload, action_id)
            result: dict[str, object] = {
                "status": state,
                "authority_available": state == "AWAITING_LOCAL_CONFIRMATION",
                "action_id": action_id,
                "code": payload["code"],
                "message": payload["message"],
            }
            remembered = self._lending_requests.get(action_id)
            if state == "FAILED" and remembered is not None:
                result["retry"] = {
                    "available": True,
                    "automatic": False,
                    "requires_user_confirmation": True,
                    "fresh_preflight": True,
                    "new_action_id": True,
                    "request": remembered,
                }
                result["next_step"] = (
                    "Explain the failure using code and message, then ask whether the user "
                    "wants to repeat this Lending request. Only after explicit confirmation, "
                    "call holon_lending_execute with retry.request; it creates a new action."
                )
            elif state in {"COMPLETED", "REJECTED", "REFUSED"}:
                self._lending_requests.pop(action_id, None)
            return json.dumps(
                result,
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

    def handle_recover_transfer(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del kwargs
        if not isinstance(params, dict) or set(params) != {"action_id"}:
            return self._safe_transfer_failure()
        action_id = params.get("action_id")
        if not isinstance(action_id, str):
            return self._safe_transfer_failure()
        try:
            response = self._connector.recover_transfer(action_id)
        except Exception:
            return self._safe_transfer_failure()
        payload = response.payload
        if (
            response.kind is MessageKind.ACTION_STATUS
            and payload.get("guard_state") == GuardState.NORMAL.value
            and payload.get("code") == "RECOVERY_COMPLETED"
        ):
            self._observe_action_payload(payload, action_id)
            return json.dumps(
                {
                    "status": "RECOVERED",
                    "authority_available": False,
                    "action_id": action_id,
                    "code": "RECOVERY_COMPLETED",
                    "message": "The interrupted transfer is invalid. Start a new transfer if needed.",
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
        return json.dumps(
            {
                "status": "REFUSED",
                "authority_available": False,
                "action_id": action_id,
                "code": payload.get("code", "RECOVERY_UNAVAILABLE"),
                "message": payload.get("message", "Transfer recovery is unavailable."),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )

    def _observe_action_payload(
        self, payload: dict[str, Any], action_id: str,
    ) -> None:
        try:
            state = GuardState(payload["guard_state"])
        except (KeyError, TypeError, ValueError):
            return
        if state in PROTECTED_STATES:
            self._protected_latch = True
            if self._protected_action_id is None:
                self._protected_action_id = action_id
            return
        if state is GuardState.NORMAL and self._protected_action_id in {None, action_id}:
            self._protected_latch = False
            self._protected_action_id = None

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
        if tool_name in PROTECTED_TOOL_ALLOWLIST:
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
            "message": "[Holon] Tools are blocked while a protected Wallet transfer may be active. Use Holon status, cancel, or recovery.",
        }


_runtime = PluginRuntime(GuardConnector(PipeGuardClient(), production_launcher()))


def _handle_health(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_health(params, **kwargs)


def _handle_open_wallet(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_open_wallet(params, **kwargs)


def _handle_wallet_balances(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_wallet_balances(params, **kwargs)


def _handle_lending_compare(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_lending_compare(params, **kwargs)


def _handle_lending_positions(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_lending_positions(params, **kwargs)


def _handle_lending_portfolio(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_lending_portfolio(params, **kwargs)


def _handle_lending_prepare(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_lending_prepare(params, **kwargs)


def _handle_lending_execute(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_lending_execute(params, **kwargs)


def _handle_prepare_transfer(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_prepare_transfer(params, **kwargs)


def _handle_transfer_status(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_transfer_status(params, **kwargs)


def _handle_cancel_transfer(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_cancel_transfer(params, **kwargs)


def _handle_recover_transfer(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_recover_transfer(params, **kwargs)


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
    empty_parameters = {
        "type": "object", "properties": {}, "required": [],
        "additionalProperties": False,
    }
    ctx.register_tool(
        name=LENDING_COMPARE_TOOL,
        toolset="holon",
        schema={
            "name": LENDING_COMPARE_TOOL,
            "description": (
                "Compare verified read-only Base USDC yield for Aave V3, Compound III, "
                "and the selected Morpho Vault. Explain freshness, unknown bonuses, and "
                "rate-model differences. Use the returned confirmed-total recommendation."
            ),
            "parameters": {
                "type": "object",
                "properties": {"force_refresh": {"type": "boolean", "default": False}},
                "required": [], "additionalProperties": False,
            },
        },
        handler=_handle_lending_compare,
        description="Compare supported Base USDC lending yield.",
    )
    ctx.register_tool(
        name=LENDING_POSITIONS_TOOL,
        toolset="holon",
        schema={
            "name": LENDING_POSITIONS_TOOL,
            "description": (
                "Read public Base USDC positions for the active Holon Account in "
                "Aave V3, Compound III, and the selected Morpho Vault without unlocking Wallet."
            ),
            "parameters": empty_parameters,
        },
        handler=_handle_lending_positions,
        description="Read supported public Lending positions.",
    )
    ctx.register_tool(
        name=LENDING_PORTFOLIO_TOOL,
        toolset="holon",
        schema={
            "name": LENDING_PORTFOLIO_TOOL,
            "description": (
                "Read the active Account's combined Base USDC Lending portfolio, "
                "tracked earnings, current confirmed yield, and optional local history "
                "for Aave V3, Compound III, and Morpho V1 without unlocking Wallet."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "force_refresh": {"type": "boolean", "default": False},
                    "history_period": {
                        "type": "string", "enum": ["none", "7d", "30d", "all"],
                        "default": "none",
                    },
                },
                "required": [], "additionalProperties": False,
            },
        },
        handler=_handle_lending_portfolio,
        description="Read the combined public Lending portfolio and analytics.",
    )
    ctx.register_tool(
        name=LENDING_PREPARE_TOOL,
        toolset="holon",
        schema={
            "name": LENDING_PREPARE_TOOL,
            "description": (
                "Prepare a non-executable Base USDC Lending preview for the explicitly "
                "confirmed protocol. If the user did not name a protocol, first call "
                "holon_lending_compare, explain its recommendation, and ask for confirmation. "
                "This never signs, broadcasts, or unlocks Wallet authority."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "protocol": {"type": "string", "enum": list(LENDING_WRITE_PROFILES)},
                    "action": {"type": "string", "enum": ["supply", "withdraw"]},
                    "amount_mode": {"type": "string", "enum": ["exact", "all"]},
                    "amount": {"type": ["string", "null"]},
                },
                "required": ["protocol", "action", "amount_mode", "amount"],
                "additionalProperties": False,
            },
        },
        handler=_handle_lending_prepare,
        description="Prepare a read-only Base USDC Lending action preview.",
    )
    ctx.register_tool(
        name=LENDING_EXECUTE_TOOL, toolset="holon",
        schema={
            "name": LENDING_EXECUTE_TOOL,
            "description": (
                "Prepare one protected Base USDC Lending operation for an explicitly confirmed "
                "protocol. If no protocol was chosen, compare first and wait for confirmation. "
                "After the user returns from Wallet, call holon_action_status for the returned "
                "action_id. Never repeat a failed action without a new explicit user confirmation."
            ),
            "parameters": {
                "type": "object", "properties": {
                    "protocol": {"type": "string", "enum": list(LENDING_WRITE_PROFILES)},
                    "action": {"type": "string", "enum": ["supply", "withdraw"]},
                    "amount_mode": {"type": "string", "enum": ["exact", "all"]},
                    "amount": {"type": ["string", "null"]},
                }, "required": ["protocol", "action", "amount_mode", "amount"],
                "additionalProperties": False,
            },
        }, handler=_handle_lending_execute,
        description="Prepare one protected Base USDC Lending supply or withdraw operation.",
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
    ctx.register_tool(
        name=RECOVER_TRANSFER_TOOL,
        toolset="holon",
        schema={
            "name": RECOVER_TRANSFER_TOOL,
            "description": (
                "Invalidate an interrupted transfer after Guard reports recovery required. "
                "Recovery never resumes or retries the transfer."
            ),
            "parameters": action_parameters,
        },
        handler=_handle_recover_transfer,
        description="Finish safe recovery for an interrupted transfer.",
    )
    for name, handler, description in (
        (
            ACTION_STATUS_TOOL, _handle_transfer_status,
            "Read protected action status. A failed Lending action may return a safe retry "
            "proposal that requires explicit user confirmation and a fresh action.",
        ),
        (CANCEL_ACTION_TOOL, _handle_cancel_transfer, "Cancel a generic protected action."),
        (RECOVER_ACTION_TOOL, _handle_recover_transfer, "Recover a generic interrupted action."),
    ):
        ctx.register_tool(
            name=name, toolset="holon",
            schema={"name": name, "description": description, "parameters": action_parameters},
            handler=handler, description=description,
        )
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
