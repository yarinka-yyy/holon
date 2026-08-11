from __future__ import annotations

import json
from pathlib import Path

import pytest

from holon_contracts import ContractViolation, MessageKind, make_envelope
from holon_guard.authority import AuthorityService
from holon_hermes_plugin import plugin
from holon_modules import ModuleLifecycleState, build_composition, load_registry


ROOT = Path(__file__).parents[1]
MOCK_ROOT = ROOT / "modules" / "mock"
PERPDEX_ROOT = ROOT / "modules" / "perpdex"


def _mock_composition(tmp_path: Path, *, disabled: bool = False) -> Path:
    destination = tmp_path / ("disabled" if disabled else "ready")
    build_composition(
        destination,
        "mock-disabled" if disabled else "mock",
        [MOCK_ROOT],
        disabled_module_ids=["holon.mock"] if disabled else [],
    )
    return destination


def _request(params: dict[str, object] | None = None):
    return make_envelope(MessageKind.MODULE_READ_REQUEST, {
        "module_id": "holon.mock",
        "capability_id": "holon.mock.read",
        "operation": "status",
        "params": params or {},
    })


def test_guard_executes_only_ready_declared_public_reader(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    composition = _mock_composition(tmp_path)
    monkeypatch.syspath_prepend(str(composition / "modules/holon.mock/src"))
    registry = load_registry(composition / "module-catalog.json", "guard")
    assert registry.module_status("holon.mock").state is ModuleLifecycleState.READY
    service = object.__new__(AuthorityService)
    service.module_registry = registry

    response = service.handle(_request(), None)
    unavailable = service.handle(make_envelope(MessageKind.MODULE_READ_REQUEST, {
        "module_id": "holon.mock", "capability_id": "holon.mock.read",
        "operation": "unknown", "params": {},
    }), None)

    assert response.kind is MessageKind.MODULE_READ_RESPONSE
    assert response.payload["status"] == "READY"
    assert response.payload["result"] == {
        "module_id": "holon.mock", "network_used": False, "status": "READY",
    }
    assert unavailable.payload["status"] == "UNAVAILABLE"
    assert unavailable.payload["result"] == {}


def test_guard_contains_secret_like_or_noncanonical_module_result(tmp_path: Path) -> None:
    composition = _mock_composition(tmp_path)
    registry = load_registry(
        composition / "module-catalog.json", "guard",
        importer=lambda _entry: (
            lambda _operation, _params: {"private_key": "must-not-cross", "rate": 1.5}
        ),
    )
    service = object.__new__(AuthorityService)
    service.module_registry = registry

    response = service.handle(_request(), None)

    assert response.payload == {
        "status": "UNAVAILABLE",
        "module_id": "holon.mock",
        "capability_id": "holon.mock.read",
        "operation": "status",
        "result": {},
        "code": "CAPABILITY_UNAVAILABLE",
        "message": "Optional module read is unavailable.",
    }
    with pytest.raises(ContractViolation):
        _request({"password": "must-not-cross"})


class _Context:
    def __init__(self) -> None:
        self.tools: list[dict[str, object]] = []
        self.hooks: list[str] = []

    def register_tool(self, **values) -> None:
        self.tools.append(values)

    def register_hook(self, name, _handler) -> None:
        self.hooks.append(name)


class _Connector:
    def module_read(self, module_id, capability_id, operation, params):
        assert params == {}
        return make_envelope(MessageKind.MODULE_READ_RESPONSE, {
            "status": "READY", "module_id": module_id,
            "capability_id": capability_id, "operation": operation,
            "result": {"module_id": module_id, "status": "READY"},
            "code": "MODULE_READ_READY", "message": "Optional module read completed.",
        })


def test_hermes_registers_only_catalog_backed_declarative_mock_tool(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    ready = _mock_composition(tmp_path)
    registry = load_registry(ready / "module-catalog.json", "hermes")
    monkeypatch.setattr(plugin, "_optional_module_registry", lambda: registry)
    monkeypatch.setattr(plugin, "_runtime", plugin.PluginRuntime(_Connector()))
    context = _Context()

    plugin.register(context)

    optional = next(item for item in context.tools if item["name"] == "holon_mock_status")
    assert optional["schema"] == {
        "name": "holon_mock_status",
        "description": "Return the deterministic read-only M7 mock module status.",
        "parameters": {
            "additionalProperties": False, "properties": {},
            "required": [], "type": "object",
        },
    }
    result = json.loads(optional["handler"](
        {}, task_id="task", session_id="session", user_task="read status",
    ))
    assert result["status"] == "READY"
    assert result["result"] == {"module_id": "holon.mock", "status": "READY"}

    disabled = _mock_composition(tmp_path / "other", disabled=True)
    disabled_registry = load_registry(disabled / "module-catalog.json", "hermes")
    monkeypatch.setattr(plugin, "_optional_module_registry", lambda: disabled_registry)
    assert plugin._optional_tool_declarations() == ()


class _PerpDexConnector(_Connector):
    def __init__(self) -> None:
        self.previews = []
        self.executions = []

    def module_action_preview(self, module_id, capability_id, action_type, params):
        self.previews.append((module_id, capability_id, action_type, params))
        return make_envelope(MessageKind.MODULE_ACTION_PREVIEW, {
            "status": "PREVIEW_READY", "authority_available": True,
            "execution_available": True, "module_id": module_id,
            "capability_id": capability_id, "action_type": action_type,
            "account": {"address": "0x" + "12" * 20, "label": "Main"},
            "preview": {"action_type": action_type},
            "preview_digest": "b" * 64, "expires_at": "2099-01-01T00:00:00.000Z",
            "checks": ["HLP_IDENTITY_VERIFIED"], "caveats": ["RISK_NOT_ASSESSED"],
            "code": "MODULE_ACTION_PREVIEW_READY", "message": "Preview ready.",
        })

    def module_action_execute(
        self, module_id, capability_id, action_type, params, digest, action_id,
    ):
        self.executions.append(
            (module_id, capability_id, action_type, params, digest, action_id),
        )
        return make_envelope(MessageKind.PROTECTED_FLOW_STARTED, {
            "guard_state": "ACTIVE", "action_state": "AWAITING_LOCAL_CONFIRMATION",
            "flow_id": "11111111-1111-4111-8111-111111111111",
            "code": "AWAITING_LOCAL_CONFIRMATION", "message": "Action ready.",
        }, action_id=action_id)


def test_hermes_perpdex_prepare_then_execute_consumes_preview_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    composition = tmp_path / "extended"
    build_composition(composition, "extended", [PERPDEX_ROOT])
    registry = load_registry(composition / "module-catalog.json", "hermes")
    connector = _PerpDexConnector()
    runtime = plugin.PluginRuntime(connector)
    monkeypatch.setattr(plugin, "_optional_module_registry", lambda: registry)
    monkeypatch.setattr(plugin, "_runtime", runtime)
    context = _Context()

    plugin.register(context)

    optional = {
        item["name"]: item for item in context.tools
        if item["name"].startswith("holon_perpdex_")
    }
    assert set(optional) == {
        "holon_perpdex_execute", "holon_perpdex_markets",
        "holon_perpdex_portfolio", "holon_perpdex_prepare",
        "holon_perpdex_fund_prepare", "holon_perpdex_fund_execute",
    }
    dispatch_context = {
        "task_id": "task", "session_id": "session", "user_task": "perpdex",
    }
    closed = json.loads(optional["holon_perpdex_prepare"]["handler"]({
        "action_type": "CLOSE_POSITION", "amount_mode": "FULL", "market": "BTC",
    }, **dispatch_context))
    assert closed["status"] == "PREVIEW_READY"
    assert connector.previews[-1][2:] == (
        "CLOSE_POSITION", {"amount_mode": "FULL", "market": "BTC", "percent": None},
    )
    opened = json.loads(optional["holon_perpdex_prepare"]["handler"]({
        "action_type": "OPEN_POSITION", "leverage": 2, "market": "ETH",
        "notional_usdc": "6", "side": "LONG",
    }, **dispatch_context))
    assert opened["status"] == "PREVIEW_READY"
    assert connector.previews[-1][2:] == ("OPEN_POSITION", {
        "leverage": 2, "market": "ETH", "notional_usdc": "6", "side": "LONG",
    })
    withdrawn = json.loads(optional["holon_perpdex_prepare"]["handler"]({
        "action_type": "HLP_WITHDRAW", "amount_mode": "ALL",
    }, **dispatch_context))
    assert withdrawn["status"] == "PREVIEW_READY"
    assert connector.previews[-1][2:] == (
        "HLP_WITHDRAW", {"amount_mode": "ALL", "amount_usdc": None},
    )
    prepared = json.loads(optional["holon_perpdex_prepare"]["handler"]({
        "action_type": "HLP_DEPOSIT", "amount_usdc": "25",
    }, **dispatch_context))
    assert prepared["status"] == "PREVIEW_READY"
    assert prepared["confirmation_required"] is True
    assert connector.previews[-1][2:] == ("HLP_DEPOSIT", {"amount_usdc": "25"})

    executed = json.loads(optional["holon_perpdex_execute"]["handler"]({
        "preview_digest": prepared["preview_digest"],
    }, **dispatch_context))
    assert executed["status"] == "AWAITING_LOCAL_CONFIRMATION"
    assert len(connector.executions) == 1
    repeated = json.loads(optional["holon_perpdex_execute"]["handler"]({
        "preview_digest": prepared["preview_digest"],
    }, **dispatch_context))
    assert repeated["code"] == "MODULE_ACTION_PREVIEW_UNKNOWN"
    assert len(connector.executions) == 1

    runtime = plugin.PluginRuntime(connector)
    monkeypatch.setattr(plugin, "_runtime", runtime)
    funding_context = _Context()
    plugin.register(funding_context)
    funding_tools = {item["name"]: item for item in funding_context.tools}
    invalid_funding = json.loads(funding_tools["holon_perpdex_fund_prepare"]["handler"]({
        "amount_usdc": 25.0,
    }, **dispatch_context))
    assert invalid_funding["code"] == "MODULE_ACTION_PREVIEW_INVALID"
    funding = json.loads(funding_tools["holon_perpdex_fund_prepare"]["handler"]({
        "amount_usdc": "25",
    }, **dispatch_context))
    assert funding["status"] == "PREVIEW_READY"
    assert funding["confirmation_required"] is False
    assert "Immediately call holon_perpdex_fund_execute" in funding["next_step"]
    assert "chat confirmation" in funding["next_step"]
    assert connector.previews[-1] == (
        "holon.perpdex", "holon.perpdex.funding.guard",
        "FUND_TRADING_ACCOUNT", {"amount_usdc": "25"},
    )
    funded = json.loads(funding_tools["holon_perpdex_fund_execute"]["handler"]({
        "preview_digest": funding["preview_digest"],
    }, **dispatch_context))
    assert funded["status"] == "AWAITING_LOCAL_CONFIRMATION"
    assert connector.executions[-1][1:4] == (
        "holon.perpdex.funding.guard", "FUND_TRADING_ACCOUNT", {"amount_usdc": "25"},
    )
