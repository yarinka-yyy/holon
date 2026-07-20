from __future__ import annotations

import os

os.environ.setdefault("QT_PREFERRED_PHYSICAL_DEVICE", "cpu")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest
from PySide6.QtCore import QObject, QMetaObject, Qt
from PySide6.QtGui import QGuiApplication

from holon_wallet.application import WalletApplication


@pytest.fixture(scope="module")
def qt_app() -> QGuiApplication:
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture
def wallet_app(qt_app: QGuiApplication):
    app = WalletApplication(qt_app)
    qt_app.processEvents()
    yield app
    app.close()


def child(app: WalletApplication, name: str) -> QObject:
    found = app.window.findChild(QObject, name)
    assert found is not None, f"QML object is missing: {name}"
    return found


def invoke(item: QObject, method: str) -> None:
    assert QMetaObject.invokeMethod(item, method, Qt.DirectConnection)


def test_window_geometry_chrome_and_qml_load_cleanly(wallet_app) -> None:
    app = wallet_app

    assert app.window.title() == "Holon Wallet"
    assert (app.window.width(), app.window.height()) == (514, 686)
    assert (app.window.minimumWidth(), app.window.minimumHeight()) == (430, 575)
    assert app.window.flags() & Qt.FramelessWindowHint
    assert app.qml_warnings == []
    assert child(app, "windowDragArea")
    assert child(app, "minimizeButton")
    assert child(app, "closeButton")


def test_routes_and_future_controls_have_expected_state(wallet_app) -> None:
    app = wallet_app
    send = child(app, "sendAction")
    transactions = child(app, "transactionsAction")
    settings = child(app, "settingsAction")

    assert not send.property("enabled")
    assert not transactions.property("enabled")
    assert settings.property("enabled")
    assert not child(app, "searchCard").property("enabled")
    assert not child(app, "addAccount").property("enabled")

    invoke(settings, "trigger")
    assert app.controller.currentScreen == "wallets"
    invoke(child(app, "backButton"), "trigger")
    assert app.controller.currentScreen == "main"


def test_selection_updates_both_qml_screens_in_memory(wallet_app, qt_app) -> None:
    app = wallet_app
    label = child(app, "mainAccountLabel")

    invoke(child(app, "accountCard"), "trigger")
    qt_app.processEvents()
    assert child(app, "accountSelector").property("open")
    assert app.controller.selectProfile("trading")
    qt_app.processEvents()
    assert app.controller.activeProfileId == "trading"
    assert label.property("text") == "Trading Account"

    assert app.controller.selectProfile("savings")
    assert not app.controller.selectProfile("unknown")
    assert app.controller.activeProfileId == "savings"
    assert child(app, "walletRow_savings").property("active")


def test_resize_and_close_are_clean(wallet_app, qt_app) -> None:
    app = wallet_app
    app.window.resize(430, 575)
    qt_app.processEvents()
    assert (app.window.width(), app.window.height()) == (430, 575)


def test_application_creates_no_user_files(tmp_path, monkeypatch, qt_app) -> None:
    monkeypatch.chdir(tmp_path)
    app = WalletApplication(qt_app)
    try:
        app.controller.selectProfile("trading")
        qt_app.processEvents()
        assert list(tmp_path.iterdir()) == []
    finally:
        app.close()
