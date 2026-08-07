from __future__ import annotations

from pathlib import Path
from concurrent.futures import Future
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
from holon_wallet.module_view import ModuleViewModel, module_page_to_map
from holon_earn import EarnPortfolioService, EarnProviderRegistry, register_module_providers
from holon_wallet.earn_view import earn_portfolio_to_map, module_earn_presentations
from holon_wallet.prices import PriceSnapshot

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
        "holon.perpdex.read.wallet",
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


class _ImmediateExecutor:
    def submit(self, function, *args):
        future = Future()
        try:
            future.set_result(function(*args))
        except Exception as exc:
            future.set_exception(exc)
        return future


class _ModuleActionClient:
    def __init__(self) -> None:
        self.previews = []
        self.executions = []

    def module_action_preview(self, module_id, capability_id, action_type, params):
        self.previews.append((module_id, capability_id, action_type, params))
        return make_envelope(MessageKind.MODULE_ACTION_PREVIEW, {
            "status": "PREVIEW_READY", "authority_available": True,
            "execution_available": True, "module_id": module_id,
            "capability_id": capability_id, "action_type": action_type,
            "account": ACCOUNT,
            "preview": {"amount_usdc": params.get("amount_usdc", params.get("notional_usdc"))},
            "preview_digest": "a" * 64, "expires_at": "2099-01-01T00:00:00.000Z",
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
            "code": "AWAITING_LOCAL_CONFIRMATION", "message": "Action is ready.",
        }, action_id=action_id)


def test_wallet_module_view_model_has_only_fixed_reads_and_prepared_execute(
    tmp_path: Path, monkeypatch,
) -> None:
    composition = _extended(tmp_path)
    monkeypatch.syspath_prepend(str(PERPDEX_ROOT / "src"))
    registry = load_registry(composition / "module-catalog.json", "wallet")
    registry.resolve("holon.perpdex.read.wallet").contribution._post = FakeInfo()
    registry.resolve("holon.perpdex.action.wallet").contribution.configure(tmp_path / "data")
    client = _ModuleActionClient()
    page = module_page_to_map(
        registry, account_provider=lambda: ACCOUNT, action_client=client,
        executor=_ImmediateExecutor(),
    )
    model = page["model"]
    assert isinstance(model, ModuleViewModel)

    assert model.refresh()
    assert [item["market"] for item in model.markets] == ["BTC", "ETH", "SOL"]
    assert model.portfolio["account"] == {
        "address": ACCOUNT["address"].lower(), "label": "Main",
    }
    assert model.operationHistory == []

    assert model.prepareHlpDeposit("25")
    assert model.prepared["preview_digest"] == "a" * 64
    assert client.previews[-1][2:] == ("HLP_DEPOSIT", {"amount_usdc": "25"})
    assert model.executePrepared()
    assert model.prepared == {}
    assert len(client.executions) == 1
    assert not model.executePrepared()
    assert model.prepareOpenPosition("BTC", "LONG", "250", 40, "CROSS")
    assert client.previews[-1][2:] == ("OPEN_POSITION", {
        "market": "BTC", "side": "LONG", "notional_usdc": "250",
        "leverage": 40, "margin_mode": "CROSS",
    })


def test_extended_hlp_contributes_one_labelled_vault_position_to_earn(
    tmp_path: Path, monkeypatch,
) -> None:
    composition = _extended(tmp_path)
    monkeypatch.syspath_prepend(str(PERPDEX_ROOT / "src"))
    module_registry = load_registry(composition / "module-catalog.json", "wallet")
    provider = module_registry.resolve("holon.perpdex.earn.hlp.wallet").contribution
    provider._reader._post = FakeInfo()
    registry = EarnProviderRegistry()
    register_module_providers(registry, module_registry)

    snapshot = EarnPortfolioService(registry).read(ACCOUNT)
    assert snapshot.total_complete is True
    assert len(snapshot.products) == 1
    product = snapshot.products[0]
    assert product.position.value_usd == "20"
    assert product.network_id == "hyperliquid-mainnet"
    mapped = earn_portfolio_to_map(
        snapshot, PriceSnapshot.unavailable(0, "NOT_NEEDED"), "vaults",
        module_earn_presentations(module_registry),
    )
    assert [item["id"] for item in mapped["availableFilters"]] == [
        "all", "lending", "vaults",
    ]
    assert len(mapped["vaultProducts"]) == 1
    assert mapped["vaultProducts"][0]["metricLabel"] == "Protocol APR"
    assert mapped["vaultProducts"][0]["riskState"] == "Not assessed"
    assert mapped["vaultProducts"][0]["badge"] == "PerpDEX Vault · Hyperliquid"
    assert mapped["vaultProducts"][0]["logoSource"].endswith("hyperliquid-blob.svg")


def test_base_has_no_perpdex_resource_or_earn_presentation(tmp_path: Path) -> None:
    composition = tmp_path / "base"
    build_composition(composition, "base")
    registry = load_registry(composition / "module-catalog.json", "wallet")

    assert not (composition / "modules" / "holon.perpdex").exists()
    assert module_earn_presentations(registry) == {}


def test_perpdex_qml_exposes_live_margin_controls_and_clean_module_header() -> None:
    page = (PERPDEX_ROOT / "wallet" / "PerpDexPage.qml").read_text(encoding="utf-8")
    review = (ROOT / "src" / "holon_wallet" / "qml" / "ProtectedActionReviewPage.qml").read_text(
        encoding="utf-8",
    )
    host = (ROOT / "src" / "holon_wallet" / "qml" / "ModulePageHost.qml").read_text(
        encoding="utf-8",
    )

    assert 'property string marginMode: "ISOLATED"' in page
    assert 'label: "Isolated"' in page and 'label: "Cross"' in page
    assert "currentMaxLeverage" in page
    assert 'objectName: "perpDexLeverageSlider"' in page
    assert "Slider {" not in page and "function updateValue(position)" in page
    assert "return 2" in page
    assert 'text: "Spread "' in page and "Funding " not in page
    assert "(maximum 100)" not in page
    assert "prepareOpenPosition(" in page and "root.marginMode" in page
    assert "canPrepareTrade()" in page
    assert 'objectName: "perpDexHlpCard"' not in page
    assert "ScrollBar.AlwaysOff" in page and 'objectName: "perpDexScrollCue"' in page
    assert "Cross margin shares PerpDEX collateral" in review
    assert "ScreenHeader" in host and "y: 126" in host
