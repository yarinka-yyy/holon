from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from holon_contracts import ContractViolation, MessageKind, make_envelope
from holon_guard.authority import AuthorityService
from holon_guard_ipc import PipeProtocolError, PipeUnavailable
from holon_hermes_plugin import plugin
from holon_hermes_plugin.guard import GuardUnavailableError
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


def test_hermes_perpdex_direct_review_and_hlp_confirmation(
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
        "holon_perpdex_fund_prepare",
    }
    dispatch_context = {
        "task_id": "task", "session_id": "session", "user_task": "perpdex",
    }
    closed = json.loads(optional["holon_perpdex_prepare"]["handler"]({
        "action_type": "CLOSE_POSITION", "amount_mode": "FULL", "market": "BTC",
    }, **dispatch_context))
    assert closed["status"] == "AWAITING_LOCAL_CONFIRMATION"
    assert "preview_digest" not in closed
    assert connector.previews[-1][2:] == (
        "CLOSE_POSITION", {"amount_mode": "FULL", "market": "BTC", "percent": None},
    )
    assert connector.executions[-1][1:5] == (
        "holon.perpdex.action.guard", "CLOSE_POSITION",
        {"amount_mode": "FULL", "market": "BTC", "percent": None}, "b" * 64,
    )

    opened_runtime = plugin.PluginRuntime(connector)
    monkeypatch.setattr(plugin, "_runtime", opened_runtime)
    opened_context = _Context()
    plugin.register(opened_context)
    open_tool = {
        item["name"]: item for item in opened_context.tools
    }["holon_perpdex_prepare"]
    opened = json.loads(open_tool["handler"]({
        "action_type": "OPEN_POSITION", "amount_usdc": "5.5",
        "leverage": 2, "margin_mode": "ISOLATED", "market": "ETH",
        "notional_usdc": "11", "side": "LONG",
    }, **dispatch_context))
    assert opened["status"] == "AWAITING_LOCAL_CONFIRMATION"
    assert "preview_digest" not in opened
    assert connector.previews[-1][2:] == ("OPEN_POSITION", {
        "leverage": 2, "margin_mode": "ISOLATED", "market": "ETH",
        "notional_usdc": "11", "side": "LONG",
    })
    properties = open_tool["schema"]["parameters"]["properties"]
    assert "user margin" in properties["amount_usdc"]["description"]
    assert "including leverage" in properties["notional_usdc"]["description"]

    margin_only = json.loads(plugin.PluginRuntime(connector).handle_module_action_prepare(
        "holon.perpdex", "holon.perpdex.action.guard", {
            "action_type": "OPEN_POSITION", "amount_usdc": "5.5",
            "leverage": 2, "market": "ETH", "side": "LONG",
        },
    ))
    assert margin_only["status"] == "AWAITING_LOCAL_CONFIRMATION"
    assert connector.previews[-1][2:] == ("OPEN_POSITION", {
        "leverage": 2, "market": "ETH", "notional_usdc": "11", "side": "LONG",
    })

    preview_count = len(connector.previews)
    mismatch = json.loads(plugin.PluginRuntime(connector).handle_module_action_prepare(
        "holon.perpdex", "holon.perpdex.action.guard", {
            "action_type": "OPEN_POSITION", "amount_usdc": "5.5",
            "leverage": 2, "market": "ETH", "notional_usdc": "12",
            "side": "LONG",
        },
    ))
    assert mismatch["code"] == "OPEN_MARGIN_NOTIONAL_MISMATCH"
    assert len(connector.previews) == preview_count

    close_all = plugin.PluginRuntime._module_action_params({
        "action_type": "CLOSE_POSITION", "amount_mode": "ALL", "market": "ETH",
    })
    assert close_all == (
        "CLOSE_POSITION",
        {"amount_mode": "FULL", "market": "ETH", "percent": None},
    )
    withdraw_full = plugin.PluginRuntime._module_action_params({
        "action_type": "HLP_WITHDRAW", "amount_mode": "FULL",
    })
    assert withdraw_full == (
        "HLP_WITHDRAW", {"amount_mode": "ALL", "amount_usdc": None},
    )

    class UnavailableConnector:
        @staticmethod
        def module_action_preview(*_args):
            return SimpleNamespace(
                kind=MessageKind.MODULE_ACTION_PREVIEW,
                payload={"status": "PREVIEW_READY", "execution_available": False},
            )

    unavailable = json.loads(
        plugin.PluginRuntime(UnavailableConnector()).handle_module_action_prepare(
            "holon.perpdex", "holon.perpdex.action.guard", {
                "action_type": "OPEN_POSITION", "leverage": 2, "market": "ETH",
                "notional_usdc": "11", "side": "LONG",
            },
        ),
    )
    assert unavailable["code"] == "MODULE_ACTION_EXECUTION_UNAVAILABLE"
    assert unavailable["stage"] == "GUARD_PREVIEW"
    assert "preview_digest" not in unavailable

    invalid_runtime = plugin.PluginRuntime(connector)
    invalid_open = json.loads(invalid_runtime.handle_module_action_prepare(
        "holon.perpdex", "holon.perpdex.action.guard", {
        "action_type": "OPEN_POSITION", "leverage": 2, "margin_mode": "UNIFIED",
        "market": "ETH", "notional_usdc": "11", "side": "LONG",
    }))
    assert invalid_open["code"] == "MODULE_ACTION_PREVIEW_INVALID"

    hlp_runtime = plugin.PluginRuntime(connector)
    monkeypatch.setattr(plugin, "_runtime", hlp_runtime)
    hlp_context = _Context()
    plugin.register(hlp_context)
    hlp_tools = {item["name"]: item for item in hlp_context.tools}
    withdrawn = json.loads(hlp_tools["holon_perpdex_prepare"]["handler"]({
        "action_type": "HLP_WITHDRAW", "amount_mode": "ALL",
    }, **dispatch_context))
    assert withdrawn["status"] == "PREVIEW_READY"
    assert connector.previews[-1][2:] == (
        "HLP_WITHDRAW", {"amount_mode": "ALL", "amount_usdc": None},
    )
    prepared = json.loads(hlp_tools["holon_perpdex_prepare"]["handler"]({
        "action_type": "HLP_DEPOSIT", "amount_usdc": "25",
    }, **dispatch_context))
    assert prepared["status"] == "PREVIEW_READY"
    assert prepared["confirmation_required"] is True
    assert connector.previews[-1][2:] == ("HLP_DEPOSIT", {"amount_usdc": "25"})

    executed = json.loads(hlp_tools["holon_perpdex_execute"]["handler"]({
        "preview_digest": prepared["preview_digest"],
    }, **dispatch_context))
    assert executed["status"] == "AWAITING_LOCAL_CONFIRMATION"
    execution_count = len(connector.executions)
    repeated = json.loads(hlp_tools["holon_perpdex_execute"]["handler"]({
        "preview_digest": prepared["preview_digest"],
    }, **dispatch_context))
    assert repeated["code"] == "MODULE_ACTION_PREVIEW_UNKNOWN"
    assert len(connector.executions) == execution_count

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
    assert funding["status"] == "AWAITING_LOCAL_CONFIRMATION"
    assert "preview_digest" not in funding
    assert connector.previews[-1] == (
        "holon.perpdex", "holon.perpdex.funding.guard",
        "FUND_TRADING_ACCOUNT", {"amount_usdc": "25"},
    )
    assert connector.executions[-1][1:4] == (
        "holon.perpdex.funding.guard", "FUND_TRADING_ACCOUNT", {"amount_usdc": "25"},
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (GuardUnavailableError("private"), "MODULE_ACTION_PREVIEW_GUARD_UNAVAILABLE"),
        (PipeUnavailable("private pipe"), "MODULE_ACTION_PREVIEW_GUARD_UNAVAILABLE"),
        (
            PipeProtocolError("private timeout", "RESPONSE_TIMEOUT"),
            "MODULE_ACTION_PREVIEW_RESPONSE_TIMEOUT",
        ),
        (PipeProtocolError("private protocol"), "MODULE_ACTION_PREVIEW_IPC_FAILED"),
        (RuntimeError("private details"), "MODULE_ACTION_PREVIEW_INTERNAL_FAILURE"),
    ],
)
def test_hermes_perpdex_preview_failures_are_specific_and_secret_free(
    error: Exception, expected: str,
) -> None:
    class FailingConnector:
        def module_action_preview(self, *_args):
            raise error

    payload = json.loads(plugin.PluginRuntime(FailingConnector()).handle_module_action_prepare(
        "holon.perpdex", "holon.perpdex.action.guard", {
            "action_type": "OPEN_POSITION", "leverage": 2, "market": "ETH",
            "notional_usdc": "12", "side": "LONG",
        },
    ))
    assert payload["code"] == expected
    assert payload["stage"] == "GUARD_PREVIEW"
    for private in ("private", "pipe", "details"):
        assert private not in json.dumps(payload).lower()
