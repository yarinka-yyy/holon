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
    result = json.loads(optional["handler"]({}))
    assert result["status"] == "READY"
    assert result["result"] == {"module_id": "holon.mock", "status": "READY"}

    disabled = _mock_composition(tmp_path / "other", disabled=True)
    disabled_registry = load_registry(disabled / "module-catalog.json", "hermes")
    monkeypatch.setattr(plugin, "_optional_module_registry", lambda: disabled_registry)
    assert plugin._optional_tool_declarations() == ()
