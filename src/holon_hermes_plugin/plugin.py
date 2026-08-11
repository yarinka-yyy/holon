"""Safe Hermes-facing capability and protected-turn hook for M2.01."""

from __future__ import annotations

import json
from collections import OrderedDict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Optional

from holon_contracts import (
    ContractEnvelope, MessageKind, load_registry, make_envelope, new_action_id,
)
from holon_guard_ipc import PipeProtocolError, PipeUnavailable
from holon_modules import (
    ModuleLifecycleState,
    decode_manifest,
    default_catalog_path,
    load_registry as load_module_registry,
    load_toolset,
)
from holon_earn import EarnPortfolioService

from .guard import (
    PROTECTED_STATES,
    GuardAvailability,
    GuardConnector,
    GuardHealth,
    GuardState,
    GuardUnavailableError,
    PipeGuardClient,
    production_launcher,
)

HEALTH_TOOL = "holon_health"
OPEN_WALLET_TOOL = "holon_open_wallet"
WALLET_BALANCES_TOOL = "holon_wallet_balances"
LENDING_COMPARE_TOOL = "holon_lending_compare"
LENDING_POSITIONS_TOOL = "holon_lending_positions"
LENDING_PORTFOLIO_TOOL = "holon_lending_portfolio"
EARN_PORTFOLIO_TOOL = "holon_earn_portfolio"
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
    "earn_portfolio",
    "action_status", "cancel_action", "recover_action",
]
PROTECTED_TOOL_ALLOWLIST = frozenset({
    HEALTH_TOOL, OPEN_WALLET_TOOL,
    TRANSFER_STATUS_TOOL, CANCEL_TRANSFER_TOOL, RECOVER_TRANSFER_TOOL,
    ACTION_STATUS_TOOL, CANCEL_ACTION_TOOL, RECOVER_ACTION_TOOL,
})
STATIC_TOOL_NAMES = frozenset({
    HEALTH_TOOL, OPEN_WALLET_TOOL, WALLET_BALANCES_TOOL,
    LENDING_COMPARE_TOOL, LENDING_POSITIONS_TOOL, LENDING_PORTFOLIO_TOOL,
    EARN_PORTFOLIO_TOOL,
    LENDING_PREPARE_TOOL, LENDING_EXECUTE_TOOL, PREPARE_TRANSFER_TOOL,
    TRANSFER_STATUS_TOOL, CANCEL_TRANSFER_TOOL, RECOVER_TRANSFER_TOOL,
    ACTION_STATUS_TOOL, CANCEL_ACTION_TOOL, RECOVER_ACTION_TOOL,
})
WALLET_OPEN_FAILURE_CODES = frozenset({
    "WALLET_EXECUTABLE_MISSING", "WALLET_START_FAILED",
    "WALLET_INITIALIZATION_FAILED", "WALLET_EXITED",
    "WALLET_INSTANCE_UNREACHABLE", "WALLET_STARTUP_TIMEOUT",
    "CONTROL_PROTOCOL_FAILED", "WALLET_PROCESS_VERIFICATION_FAILED",
    "WALLET_OPEN_INTERNAL_FAILURE", "WALLET_UNAVAILABLE",
})
WALLET_OPEN_FAILURE_MESSAGES = {
    "WALLET_EXECUTABLE_MISSING": "Wallet executable is missing.",
    "WALLET_START_FAILED": "Wallet could not be started.",
    "WALLET_INITIALIZATION_FAILED": "Wallet could not initialize.",
    "WALLET_EXITED": "Wallet exited before it became ready.",
    "WALLET_INSTANCE_UNREACHABLE": "The existing Wallet instance could not be reached.",
    "WALLET_STARTUP_TIMEOUT": "Wallet did not become ready in time.",
    "CONTROL_PROTOCOL_FAILED": "Wallet control protocol failed.",
    "WALLET_PROCESS_VERIFICATION_FAILED": "Wallet process verification failed.",
    "WALLET_OPEN_INTERNAL_FAILURE": "Wallet launch failed inside Guard.",
    "WALLET_UNAVAILABLE": "Wallet is unavailable.",
    "WALLET_GUARD_UNAVAILABLE": "Guard is unavailable.",
    "WALLET_GUARD_RESPONSE_TIMEOUT": "Guard did not respond in time.",
    "WALLET_GUARD_IPC_FAILED": "Guard communication failed.",
    "WALLET_OPEN_RESPONSE_INVALID": "Wallet launch response was invalid.",
}


def _wallet_open_failure(code: str) -> str:
    return json.dumps(
        {
            "status": "DEGRADED",
            "capabilities": CAPABILITIES,
            "authority_available": False,
            "code": code,
            "message": WALLET_OPEN_FAILURE_MESSAGES[code],
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _validated_fallback(kind: MessageKind, value: dict[str, Any]) -> dict[str, Any]:
    return dict(make_envelope(kind, value).payload)


def _unavailable_balances() -> dict[str, Any]:
    networks = []
    registry = load_registry()
    for spec in registry.networks:
        networks.append(
            {
                "network": spec.network_id,
                "chain_id": spec.chain_id,
                "status": "UNAVAILABLE",
                "block_number": None,
                "updated_at": None,
                "error_code": "WALLET_UNAVAILABLE",
                "balances": [
                    {
                        "asset_id": deployment.asset_id,
                        "asset": registry.asset_by_id[deployment.asset_id].display_symbol,
                        "status": "UNAVAILABLE",
                        "amount_atomic": None,
                        "decimals": registry.asset_by_id[deployment.asset_id].decimals,
                        "display": None,
                        "error_code": "WALLET_UNAVAILABLE",
                    }
                    for deployment in registry.deployments_by_network[spec.network_id]
                ],
            }
        )
    value = {
        "balance_schema_version": "2",
        "status": "DEGRADED",
        "authority_available": False,
        "account": None,
        "networks": networks,
        "code": "BALANCES_UNAVAILABLE",
        "message": "Wallet balances are unavailable.",
    }
    return _validated_fallback(MessageKind.WALLET_BALANCES, value)


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
    return _validated_fallback(MessageKind.LENDING_MARKETS, value)


def _unavailable_lending_preview() -> dict[str, Any]:
    value = {
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
        "position_before_atomic": None,
        "caveats": ["WALLET_UNAVAILABLE"], "code": "LENDING_ACTION_UNAVAILABLE",
        "message": "Lending action preview is unavailable.",
    }
    return _validated_fallback(MessageKind.LENDING_ACTION_PREVIEW, value)


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
    return _validated_fallback(MessageKind.LENDING_POSITIONS, value)


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
        "history": {
            "period": history_period, "granularity": "none",
            "period_start": None, "period_end": None, "points": [],
        },
        "code": "LENDING_PORTFOLIO_UNAVAILABLE",
        "message": "Lending portfolio is unavailable.",
    })
    return _validated_fallback(MessageKind.LENDING_PORTFOLIO, value)


def _unavailable_earn_portfolio() -> dict[str, Any]:
    return _validated_fallback(
        MessageKind.EARN_PORTFOLIO,
        EarnPortfolioService.unavailable(None).to_dict(),
    )


class PluginRuntime:
    def __init__(self, connector: GuardConnector) -> None:
        self._connector = connector
        self._protected_latch = False
        self._protected_action_id: str | None = None
        self._lending_requests: OrderedDict[str, dict[str, object]] = OrderedDict()
        self._module_previews: OrderedDict[str, dict[str, object]] = OrderedDict()

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

    def _begin_protected_dispatch(self, action_id: str) -> bool:
        if self._protected_latch:
            return False
        self._protected_latch = True
        self._protected_action_id = action_id
        return True

    def _finish_protected_dispatch(
        self, response: ContractEnvelope, action_id: str,
    ) -> None:
        if response.action_id != action_id or self._protected_action_id != action_id:
            return
        if response.kind is MessageKind.REFUSAL or (
            response.kind is MessageKind.SIGNING_DISABLED
            and response.payload.get("guard_state") == GuardState.SIGNING_DISABLED.value
        ):
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
            health = self._connector.ensure_available()
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
                wallet_state = payload.get("wallet_state")
                code = payload.get("code")
                if (
                    wallet_state not in {"OPENED", "ACTIVATED"}
                    or code not in {"WALLET_OPENED", "WALLET_ACTIVATED"}
                    or not isinstance(payload.get("message"), str)
                ):
                    return _wallet_open_failure("WALLET_OPEN_RESPONSE_INVALID")
                return json.dumps(
                    {
                        "status": wallet_state,
                        "capabilities": CAPABILITIES,
                        "authority_available": False,
                        "code": code,
                        "message": (
                            "Wallet activation was requested."
                            if wallet_state == "ACTIVATED"
                            else "Wallet launch was verified."
                        ),
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            if response.kind.value == "error" and payload.get("code") in WALLET_OPEN_FAILURE_CODES:
                return _wallet_open_failure(str(payload["code"]))
            return _wallet_open_failure("WALLET_OPEN_RESPONSE_INVALID")
        except GuardUnavailableError:
            return _wallet_open_failure("WALLET_GUARD_UNAVAILABLE")
        except PipeUnavailable:
            return _wallet_open_failure("WALLET_GUARD_UNAVAILABLE")
        except PipeProtocolError as error:
            code = (
                "WALLET_GUARD_RESPONSE_TIMEOUT"
                if error.code == "RESPONSE_TIMEOUT" else "WALLET_GUARD_IPC_FAILED"
            )
            return _wallet_open_failure(code)
        except Exception:
            return _wallet_open_failure("WALLET_GUARD_IPC_FAILED")

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

    def handle_module_read(
        self,
        module_id: str,
        capability_id: str,
        operation: str,
        params: Optional[dict] = None,
        **kwargs: Any,
    ) -> str:
        values: dict[str, Any] = {}
        invalid = params is not None and not isinstance(params, dict)
        if isinstance(params, dict):
            values.update(params)
        if set(values).intersection(kwargs):
            invalid = True
        values.update(kwargs)
        try:
            response = None if invalid else self._connector.module_read(
                module_id, capability_id, operation, values,
            )
            if response is None:
                raise ValueError("Invalid module parameters")
            if response.kind is MessageKind.MODULE_READ_RESPONSE:
                return json.dumps(
                    response.payload, ensure_ascii=False, separators=(",", ":"),
                )
        except Exception:
            pass
        return json.dumps({
            "status": "UNAVAILABLE",
            "module_id": module_id,
            "capability_id": capability_id,
            "operation": operation,
            "result": {},
            "code": "CAPABILITY_UNAVAILABLE",
            "message": "Optional module read is unavailable.",
        }, ensure_ascii=False, separators=(",", ":"))

    @staticmethod
    def _module_action_params(values: dict[str, Any]) -> tuple[str, dict[str, object]]:
        action_type = values.get("action_type")
        required, allowed = {
            "OPEN_POSITION": (
                {"action_type", "leverage", "market", "notional_usdc", "side"},
                {
                    "action_type", "leverage", "margin_mode", "market",
                    "notional_usdc", "side",
                },
            ),
            "CLOSE_POSITION": (
                {"action_type", "amount_mode", "market"},
                {"action_type", "amount_mode", "market", "percent"},
            ),
            "HLP_DEPOSIT": (
                {"action_type", "amount_usdc"},
                {"action_type", "amount_usdc"},
            ),
            "HLP_WITHDRAW": (
                {"action_type", "amount_mode"},
                {"action_type", "amount_mode", "amount_usdc"},
            ),
            "FUND_TRADING_ACCOUNT": (
                {"action_type", "amount_usdc"},
                {"action_type", "amount_usdc"},
            ),
        }.get(action_type, (set(), set()))
        if not required or not required.issubset(values) or not set(values).issubset(allowed):
            raise ValueError("Invalid protected module parameters")
        if action_type == "OPEN_POSITION":
            if (
                values["market"] not in {"BTC", "ETH", "SOL"}
                or values["side"] not in {"LONG", "SHORT"}
                or type(values["leverage"]) is not int
                or values["leverage"] < 1
                or values.get("margin_mode") not in {None, "CROSS", "ISOLATED"}
                or not isinstance(values["notional_usdc"], str)
            ):
                raise ValueError("Invalid open parameters")
            params = {key: values[key] for key in (
                "leverage", "market", "notional_usdc", "side",
            )}
            if "margin_mode" in values:
                params["margin_mode"] = values["margin_mode"]
        elif action_type == "CLOSE_POSITION":
            percent = values.get("percent")
            if (
                values["market"] not in {"BTC", "ETH", "SOL"}
                or values["amount_mode"] not in {"FULL", "PERCENT"}
                or values["amount_mode"] == "FULL" and percent is not None
                or values["amount_mode"] == "PERCENT"
                and not isinstance(percent, str)
            ):
                raise ValueError("Invalid close parameters")
            params = {
                "amount_mode": values["amount_mode"], "market": values["market"],
                "percent": percent,
            }
        elif action_type == "HLP_DEPOSIT":
            if not isinstance(values["amount_usdc"], str):
                raise ValueError("Invalid HLP deposit parameters")
            params = {"amount_usdc": values["amount_usdc"]}
        elif action_type == "HLP_WITHDRAW":
            amount_usdc = values.get("amount_usdc")
            if (
                values["amount_mode"] not in {"EXACT", "ALL"}
                or values["amount_mode"] == "ALL" and amount_usdc is not None
                or values["amount_mode"] == "EXACT"
                and not isinstance(amount_usdc, str)
            ):
                raise ValueError("Invalid HLP withdrawal parameters")
            params = {
                "amount_mode": values["amount_mode"],
                "amount_usdc": amount_usdc,
            }
        else:
            if not isinstance(values["amount_usdc"], str):
                raise ValueError("Invalid funding parameters")
            params = {"amount_usdc": values["amount_usdc"]}
        return str(action_type), params

    def handle_module_action_prepare(
        self, module_id: str, capability_id: str,
        params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        values: dict[str, Any] = {}
        if not isinstance(params, dict) or set(params).intersection(kwargs):
            return self._module_action_failure("MODULE_ACTION_PREVIEW_INVALID")
        values.update(params)
        values.update(kwargs)
        return self._prepare_module_action(module_id, capability_id, values)

    def handle_module_funding_prepare(
        self, module_id: str, capability_id: str,
        params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        values: dict[str, Any] = {}
        if not isinstance(params, dict) or set(params).intersection(kwargs):
            return self._module_action_failure("MODULE_ACTION_PREVIEW_INVALID")
        values.update(params)
        values.update(kwargs)
        if set(values) != {"amount_usdc"} or not isinstance(values["amount_usdc"], str):
            return self._module_action_failure("MODULE_ACTION_PREVIEW_INVALID")
        return self._prepare_module_action(
            module_id, capability_id,
            {"action_type": "FUND_TRADING_ACCOUNT", **values},
            execute_tool="holon_perpdex_fund_execute",
        )

    def _prepare_module_action(
        self, module_id: str, capability_id: str, values: dict[str, Any], *,
        execute_tool: str = "holon_perpdex_execute",
    ) -> str:
        try:
            action_type, semantic = self._module_action_params(values)
        except ValueError:
            return self._module_action_failure("MODULE_ACTION_PREVIEW_INVALID")
        try:
            response = self._connector.module_action_preview(
                module_id, capability_id, action_type, semantic,
            )
            if (
                response.kind is not MessageKind.MODULE_ACTION_PREVIEW
                or response.payload["status"] != "PREVIEW_READY"
                or response.payload["execution_available"] is not True
            ):
                return json.dumps(
                    response.payload, ensure_ascii=False, separators=(",", ":"),
                )
            payload = dict(response.payload)
            digest = str(payload["preview_digest"])
            self._module_previews[digest] = {
                "action_type": action_type, "capability_id": capability_id,
                "expires_at": payload["expires_at"], "module_id": module_id,
                "params": semantic,
            }
            self._module_previews.move_to_end(digest)
            while len(self._module_previews) > 32:
                self._module_previews.popitem(last=False)
            if execute_tool == "holon_perpdex_fund_execute":
                payload.update({
                    "confirmation_required": False,
                    "next_step": (
                        "Immediately call holon_perpdex_fund_execute once with this "
                        "preview_digest in the same user turn. Do not show the preview or "
                        "ask for chat confirmation. This opens Wallet Review only; the "
                        "fresh local password and Wallet confirmation remain required."
                    ),
                })
            else:
                payload.update({
                    "confirmation_required": True,
                    "next_step": (
                        "Explain the exact preview and risks. Only after explicit confirmation "
                        f"in a later user message call {execute_tool} once with preview_digest."
                    ),
                })
            return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except GuardUnavailableError:
            return self._module_action_failure("MODULE_ACTION_PREVIEW_GUARD_UNAVAILABLE")
        except PipeUnavailable:
            return self._module_action_failure("MODULE_ACTION_PREVIEW_GUARD_UNAVAILABLE")
        except PipeProtocolError as error:
            code = (
                "MODULE_ACTION_PREVIEW_RESPONSE_TIMEOUT"
                if error.code == "RESPONSE_TIMEOUT"
                else "MODULE_ACTION_PREVIEW_IPC_FAILED"
            )
            return self._module_action_failure(code)
        except (AttributeError, KeyError, TypeError, ValueError):
            return self._module_action_failure("MODULE_ACTION_PREVIEW_RESPONSE_INVALID")
        except Exception:
            return self._module_action_failure("MODULE_ACTION_PREVIEW_INTERNAL_FAILURE")

    @staticmethod
    def _module_action_failure(code: str, action_id: str | None = None) -> str:
        messages = {
            "MODULE_ACTION_PREVIEW_INVALID": "Protected action parameters are invalid.",
            "MODULE_ACTION_PREVIEW_GUARD_UNAVAILABLE": "Local Guard is unavailable.",
            "MODULE_ACTION_PREVIEW_RESPONSE_TIMEOUT": "Local Guard did not respond in time.",
            "MODULE_ACTION_PREVIEW_IPC_FAILED": "Local Guard response could not be verified.",
            "MODULE_ACTION_PREVIEW_RESPONSE_INVALID": "Local Guard returned an invalid preview.",
            "MODULE_ACTION_PREVIEW_INTERNAL_FAILURE": "Protected action preview could not be created.",
        }
        value: dict[str, object] = {
            "status": "UNAVAILABLE", "authority_available": False,
            "code": code,
            "message": messages.get(code, "Protected module action is unavailable."),
        }
        if action_id is not None:
            value["action_id"] = action_id
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))

    def handle_module_action_execute(
        self, module_id: str, capability_id: str,
        params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        if (
            not isinstance(params, dict) or kwargs or set(params) != {"preview_digest"}
            or not isinstance(params.get("preview_digest"), str)
        ):
            return self._module_action_failure("MODULE_ACTION_EXECUTE_INVALID")
        digest = str(params["preview_digest"])
        prepared = self._module_previews.pop(digest, None)
        if (
            prepared is None or prepared["module_id"] != module_id
            or prepared["capability_id"] != capability_id
        ):
            return self._module_action_failure("MODULE_ACTION_PREVIEW_UNKNOWN")
        try:
            expires = datetime.fromisoformat(
                str(prepared["expires_at"]).removesuffix("Z") + "+00:00",
            )
        except ValueError:
            expires = datetime.min.replace(tzinfo=UTC)
        if expires <= datetime.now(UTC):
            return self._module_action_failure("MODULE_ACTION_PREVIEW_EXPIRED")
        action_id = new_action_id()
        if not self._begin_protected_dispatch(action_id):
            return self._module_action_failure("PROTECTED_FLOW_ACTIVE", action_id)
        try:
            response = self._connector.module_action_execute(
                module_id, capability_id, str(prepared["action_type"]),
                dict(prepared["params"]), digest, action_id,
            )
        except Exception:
            if self._protected_action_id == action_id:
                self._protected_latch = False
                self._protected_action_id = None
            return self._module_action_failure("MODULE_ACTION_EXECUTE_UNAVAILABLE", action_id)
        self._finish_protected_dispatch(response, action_id)
        if response.kind is MessageKind.PROTECTED_FLOW_STARTED:
            return json.dumps({
                "status": "AWAITING_LOCAL_CONFIRMATION",
                "authority_available": True, "action_id": action_id,
                "action_type": prepared["action_type"], "code": response.payload["code"],
                "message": "Review the exact Hyperliquid bundle in Wallet.",
                "turn_state": "END_REQUIRED",
                "next_step": (
                    "End this turn and wait for the Wallet decision. Do not retry. "
                    "When the user returns, call holon_action_status with this action_id."
                ),
            }, ensure_ascii=False, separators=(",", ":"))
        return json.dumps({
            "status": "REFUSED", "authority_available": False,
            "action_id": action_id,
            "code": response.payload.get("code", "MODULE_ACTION_REFUSED"),
            "message": response.payload.get("message", "Protected module action was refused."),
        }, ensure_ascii=False, separators=(",", ":"))

    def handle_module_funding_execute(
        self, module_id: str, capability_id: str,
        params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        return self.handle_module_action_execute(
            module_id, capability_id, params, **kwargs,
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

    def handle_earn_portfolio(
        self, params: Optional[dict] = None, **kwargs: Any,
    ) -> str:
        del kwargs
        values = {} if params is None else params
        if not isinstance(values, dict) or set(values) - {"force_refresh"}:
            return json.dumps(_unavailable_earn_portfolio(), separators=(",", ":"))
        force_refresh = values.get("force_refresh", False)
        if type(force_refresh) is not bool:
            return json.dumps(_unavailable_earn_portfolio(), separators=(",", ":"))
        try:
            response = self._connector.earn_portfolio(force_refresh)
            if response.kind is MessageKind.EARN_PORTFOLIO:
                return json.dumps(
                    response.payload, ensure_ascii=False, separators=(",", ":"),
                )
        except Exception:
            pass
        return json.dumps(
            _unavailable_earn_portfolio(), ensure_ascii=False, separators=(",", ":"),
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
        if not self._begin_protected_dispatch(action_id):
            return self._safe_transfer_failure(action_id)
        try:
            response = self._connector.lending_action_execute(intent, action_id)
        except Exception:
            return self._safe_transfer_failure(action_id)
        self._finish_protected_dispatch(response, action_id)
        if response.kind is MessageKind.PROTECTED_FLOW_STARTED:
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
        if not self._begin_protected_dispatch(action_id):
            return self._safe_transfer_failure(action_id)
        try:
            response = self._connector.prepare_transfer(dict(params), action_id)
        except Exception:
            return self._safe_transfer_failure(action_id)
        self._finish_protected_dispatch(response, action_id)
        if response.kind is MessageKind.PROTECTED_FLOW_STARTED:
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


def _optional_module_registry():
    adjacent = Path(__file__).with_name("module-catalog.json")
    return load_module_registry(
        adjacent if adjacent.is_file() else default_catalog_path(), "hermes",
    )


def _optional_tool_declarations():
    registry = _optional_module_registry()
    candidates: dict[str, list[tuple[object, str]]] = {}
    for capability in registry.capabilities("hermes_toolset"):
        module_id = capability.module_id
        if registry.module_status(module_id).state is not ModuleLifecycleState.READY:
            continue
        try:
            root = Path(capability.resource_root or "")
            manifest = decode_manifest((root / "module-manifest.json").read_bytes())
            targets = {
                item.capability_id: item for item in manifest.capabilities
                if item.kind in {"public_reader", "protected_action_adapter"}
            }
            descriptor_path = str(capability.declaration.descriptor["descriptor_path"])
            tools = list(load_toolset(root / descriptor_path))
            if any(
                tool.capability_id not in targets
                or (
                    targets[tool.capability_id].kind == "public_reader"
                    and tool.operation not in targets[tool.capability_id].descriptor["operations"]
                )
                or (
                    targets[tool.capability_id].kind == "protected_action_adapter"
                    and (
                        targets[tool.capability_id].component != "guard"
                        or tool.operation not in {"prepare", "execute"}
                        or targets[tool.capability_id].descriptor.get("adapter_version") != "1"
                        or targets[tool.capability_id].descriptor.get("profile_id")
                        not in {
                            "hyperliquid-arbitrum-funding-v1",
                            "hyperliquid-mainnet-v1",
                        }
                    )
                )
                or tool.name in STATIC_TOOL_NAMES
                for tool in tools
            ):
                continue
            candidates[module_id] = [
                (tool, targets[tool.capability_id].kind) for tool in tools
            ]
        except Exception:
            continue
    owners: dict[str, list[str]] = {}
    for module_id, tools in candidates.items():
        for tool, _kind in tools:
            owners.setdefault(tool.name, []).append(module_id)
    conflicts = {
        module_id
        for module_ids in owners.values() if len(module_ids) > 1
        for module_id in module_ids
    }
    return tuple(
        (module_id, tool, capability_kind)
        for module_id in sorted(candidates) if module_id not in conflicts
        for tool, capability_kind in candidates[module_id]
    )


def _register_optional_tools(ctx: Any) -> None:
    for module_id, tool, capability_kind in _optional_tool_declarations():
        def handler(
            params: Optional[dict] = None,
            _module_id: str = module_id,
            _capability_id: str = tool.capability_id,
            _operation: str = tool.operation,
            _capability_kind: str = capability_kind,
            _is_funding: bool = tool.capability_id == "holon.perpdex.funding.guard",
            **kwargs: Any,
        ) -> str:
            # Hermes supplies dispatch context here; module contracts accept
            # only schema-bound user parameters from the positional dictionary.
            del kwargs
            if _capability_kind == "protected_action_adapter":
                if _operation == "prepare":
                    if _is_funding:
                        return _runtime.handle_module_funding_prepare(
                            _module_id, _capability_id, params,
                        )
                    return _runtime.handle_module_action_prepare(
                        _module_id, _capability_id, params,
                    )
                if _is_funding:
                    return _runtime.handle_module_funding_execute(
                        _module_id, _capability_id, params,
                    )
                return _runtime.handle_module_action_execute(
                    _module_id, _capability_id, params,
                )
            return _runtime.handle_module_read(
                _module_id, _capability_id, _operation, params,
            )

        ctx.register_tool(
            name=tool.name,
            toolset="holon",
            schema={
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.parameters),
            },
            handler=handler,
            description=tool.description,
        )


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


def _handle_earn_portfolio(params: Optional[dict] = None, **kwargs: Any) -> str:
    return _runtime.handle_earn_portfolio(params, **kwargs)


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
        name=EARN_PORTFOLIO_TOOL,
        toolset="holon",
        schema={
            "name": EARN_PORTFOLIO_TOOL,
            "description": (
                "Read the normalized Earn portfolio across all active providers. "
                "Compare typed metrics, freshness, exit conditions, and the explicit "
                "NOT_ASSESSED risk state without unlocking Wallet."
            ),
            "parameters": {
                "type": "object",
                "properties": {"force_refresh": {"type": "boolean", "default": False}},
                "required": [], "additionalProperties": False,
            },
        },
        handler=_handle_earn_portfolio,
        description="Read the normalized public Earn portfolio.",
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
    _register_optional_tools(ctx)
    ctx.register_hook("on_session_start", _on_session_start)
    ctx.register_hook("pre_tool_call", _on_pre_tool_call)
