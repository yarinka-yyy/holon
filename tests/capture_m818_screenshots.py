"""Fixture-only visual acceptance capture for M8.18; never uses a real account or RPC."""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")
ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PySide6.QtCore import QMetaObject, QObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from holon_modules import build_composition, load_registry
from holon_wallet.history import HistoryStatus, HistoryStore, WalletHistoryRecord
from holon_wallet.perpdex_action import PerpDexExecutionResult
from holon_wallet.storage import WalletPaths
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic

sys.path.insert(0, str(Path(__file__).parent))
from test_perpdex_reader import FakeInfo  # noqa: E402
from test_wallet_ui import make_app  # noqa: E402

OUT = ROOT / "build" / "m8.18-screenshots"
ADDRESS = "0x" + "11" * 20
BRIDGE = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"


class _Intent:
    def __init__(self, kind, values):
        self.action_type, self._values = SimpleNamespace(value=kind), values

    def to_mapping(self): return dict(self._values)


def _bundle(kind, intent, phases, *, funding=False):
    value = SimpleNamespace(
        operation_id="act-11111111-1111-4111-8111-111111111111", account=ADDRESS,
        intent=_Intent(kind, intent), created_at="2026-08-11T12:00:00Z",
        expires_at="2026-08-11T12:05:00Z", disclosure="", phases=tuple(phases),
    )
    if funding:
        value.action = SimpleNamespace(amount_atomic=6_000_000, chain_id=42161,
            max_total_fee_wei=1_250_000_000_000_000, recipient=BRIDGE,
            token_contract="0xaf88d065e77c8cc2239327c5edb3a432268e5831")
    return value


def _phase(kind, semantic): return SimpleNamespace(phase_type=SimpleNamespace(value=kind), semantic=semantic)


def _capture(app, name):
    # PageState fades inactive pages; wait past that animation so the capture
    # represents the settled screen rather than a transition frame.
    app.qt_app.processEvents(); QTest.qWait(360); app.qt_app.processEvents()
    image = app.window.grabWindow()
    if image.isNull() or not image.save(str(OUT / name)):
        raise RuntimeError(f"Unable to capture {name}")


def _show_action(app, bundle, screen, result=None):
    app.controller._perpdex_bundle = bundle
    app.controller._perpdex_result = result
    app.controller.perpDexChanged.emit()
    app.controller._set_screen(screen)


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    app_data = OUT / "fixture-data"
    shutil.rmtree(app_data, ignore_errors=True)
    qt = QGuiApplication.instance() or QGuiApplication([])
    repository = VaultRepository(WalletPaths(app_data))
    repository.create_new("fixture-password", repository.new_record(generate_mnemonic(), "Fixture"))
    composition = OUT / "fixture-extended"
    shutil.rmtree(composition, ignore_errors=True)
    build_composition(composition, "extended", [ROOT / "modules" / "perpdex"])
    sys.path.insert(0, str(composition / "modules" / "holon.perpdex" / "src"))
    registry = load_registry(composition / "module-catalog.json", "wallet")
    registry.resolve("holon.perpdex.read.wallet").contribution._post = FakeInfo()
    app = make_app(qt, repository, module_registry=registry)
    try:
        funding = _bundle("FUND_TRADING_ACCOUNT", {"amount_usdc": "6"}, [
            _phase("ARBITRUM_USDC_TRANSFER", {"amount_usdc": "6"}),
        ], funding=True)
        _show_action(app, funding, "perpdex_review"); _capture(app, "01-funding-review.png")
        _show_action(app, funding, "perpdex_result", PerpDexExecutionResult(
            funding.operation_id, "FUND_TRADING_ACCOUNT", "FAILED", "FUNDING_REVALIDATION_FAILED", "", ()))
        _capture(app, "02-funding-result-stopped.png")
        _show_action(app, funding, "perpdex_result", PerpDexExecutionResult(
            funding.operation_id, "FUND_TRADING_ACCOUNT", "PENDING_CREDIT", "FUNDING_BROADCAST_PENDING", "", ({
                "phaseId": "phase-1", "phaseType": "ARBITRUM_USDC_TRANSFER", "state": "PENDING_CREDIT",
                "code": "FUNDING_BROADCAST_PENDING", "publicId": "0x" + "aa" * 32},)))
        _capture(app, "03-funding-result-sent.png")
        order = _phase("PLACE_IOC_ORDER", {"market": "ETH", "is_buy": True, "size_asset": "0.0062",
            "reference_price": "1887", "limit_price": "1905", "max_slippage_percent": "1", "reduce_only": False})
        leverage = _phase("SET_ISOLATED_LEVERAGE", {"is_cross": False, "leverage": 2})
        opening = _bundle("OPEN_POSITION", {"market": "ETH", "notional_usdc": "12", "leverage": 2}, [leverage, order])
        _show_action(app, opening, "perpdex_review"); _capture(app, "04-open-position-review.png")
        close_order = _phase("PLACE_IOC_ORDER", {**order.semantic, "is_buy": False, "reduce_only": True,
            "position_size_before_asset": "0.0062", "position_side": "LONG"})
        closing = _bundle("CLOSE_POSITION", {"market": "ETH"}, [close_order])
        _show_action(app, closing, "perpdex_password"); _capture(app, "05-close-position-password.png")
        _show_action(app, closing, "perpdex_result", PerpDexExecutionResult(
            closing.operation_id, "CLOSE_POSITION", "COMPLETED", "PERPDEX_ACTION_COMPLETED", "", ()))
        _capture(app, "06-position-result.png")
        store = HistoryStore(repository.paths)
        profile = app.controller.activeProfile["id"]
        store.append(WalletHistoryRecord("act-history", profile, "perpdex_funding", "arbitrum", 42161,
            ADDRESS, BRIDGE, "0xaf88d065e77c8cc2239327c5edb3a432268e5831", "USDC", "6000000", 6,
            None, HistoryStatus.FAILED, "2026-08-11T12:00:00Z", "2026-08-11T12:00:00Z", False,
            operation_id="act-history"))
        app.controller.showHistory(); _capture(app, "07-history-all.png")
        button = app.window.findChild(QObject, "historyPerpDexTab")
        QMetaObject.invokeMethod(button, "trigger", Qt.DirectConnection); _capture(app, "08-history-perpdex.png")
        app.controller.showModulePage(); _capture(app, "09-dashboard-position-orders.png")
    finally:
        app.close()


if __name__ == "__main__": main()
