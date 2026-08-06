from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from holon_contracts import MessageKind, make_envelope
from holon_guard.authority import AuthorityService
from holon_modules import (
    ModuleLifecycleState,
    build_composition,
    decode_manifest,
    encode_manifest,
    load_registry,
)

from test_perpdex_reader import ACCOUNT, FakeInfo


ROOT = Path(__file__).parents[1]
PERPDEX_ROOT = ROOT / "modules" / "perpdex"


def _extended(tmp_path: Path) -> Path:
    destination = tmp_path / "extended"
    build_composition(destination, "extended", [PERPDEX_ROOT])
    return destination


def test_perpdex_manifest_is_canonical_and_registers_only_by_component(
    tmp_path: Path, monkeypatch,
) -> None:
    raw = (PERPDEX_ROOT / "module-manifest.json").read_bytes()
    manifest = decode_manifest(raw)
    assert encode_manifest(manifest) == raw
    assert manifest.module_id == "holon.perpdex"
    assert [item.capability_id for item in manifest.capabilities] == sorted(
        item.capability_id for item in manifest.capabilities
    )

    composition = _extended(tmp_path)
    monkeypatch.syspath_prepend(str(PERPDEX_ROOT / "src"))
    guard = load_registry(composition / "module-catalog.json", "guard")
    wallet = load_registry(composition / "module-catalog.json", "wallet")
    hermes = load_registry(composition / "module-catalog.json", "hermes")

    assert guard.module_status("holon.perpdex").state is ModuleLifecycleState.READY
    assert [item.declaration.capability_id for item in guard.capabilities()] == [
        "holon.perpdex.action.guard",
        "holon.perpdex.earn.hlp",
        "holon.perpdex.read",
    ]
    assert [item.declaration.capability_id for item in wallet.capabilities()] == [
        "holon.perpdex.action.wallet",
        "holon.perpdex.earn.hlp.wallet",
        "holon.perpdex.wallet",
    ]
    assert [item.declaration.capability_id for item in hermes.capabilities()] == [
        "holon.perpdex.tools",
    ]


class _Wallet:
    def read_public_balances(self):
        return SimpleNamespace(ok=True, payload={"account": ACCOUNT})


def test_guard_supplies_active_account_to_perpdex_reader(
    tmp_path: Path, monkeypatch,
) -> None:
    composition = _extended(tmp_path)
    monkeypatch.syspath_prepend(str(PERPDEX_ROOT / "src"))
    registry = load_registry(composition / "module-catalog.json", "guard")
    transport = FakeInfo()
    registry.resolve("holon.perpdex.read").contribution._post = transport
    service = object.__new__(AuthorityService)
    service.module_registry = registry
    service.lifecycle = SimpleNamespace(wallet=_Wallet())

    response = service.handle(make_envelope(MessageKind.MODULE_READ_REQUEST, {
        "module_id": "holon.perpdex",
        "capability_id": "holon.perpdex.read",
        "operation": "portfolio",
        "params": {},
    }), None)

    assert response.kind is MessageKind.MODULE_READ_RESPONSE
    assert response.payload["status"] == "READY"
    assert response.payload["result"]["account"] == {
        "address": ACCOUNT["address"].lower(), "label": "Main",
    }
    assert all(call.get("user") == ACCOUNT["address"].lower() for call in transport.calls)


def test_module_read_cannot_spoof_guard_supplied_account() -> None:
    try:
        make_envelope(MessageKind.MODULE_READ_REQUEST, {
            "module_id": "holon.perpdex",
            "capability_id": "holon.perpdex.read",
            "operation": "portfolio",
            "params": {"active_account": ACCOUNT},
        })
    except Exception as exc:
        assert getattr(exc, "code", None) == "UNKNOWN_AUTHORITY_FIELD"
    else:
        raise AssertionError("Guard-owned account field was accepted")
