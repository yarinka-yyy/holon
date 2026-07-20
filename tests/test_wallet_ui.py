from __future__ import annotations

import os
import secrets

os.environ.setdefault("QT_PREFERRED_PHYSICAL_DEVICE", "cpu")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest
from PySide6.QtCore import QObject, QMetaObject, Qt
from PySide6.QtGui import QGuiApplication

from holon_wallet.application import WalletApplication
from holon_wallet.storage import WalletPaths, atomic_write_json
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic


@pytest.fixture(scope="module")
def qt_app() -> QGuiApplication:
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture
def wallet_app(tmp_path, qt_app: QGuiApplication):
    repository = VaultRepository(WalletPaths(tmp_path))
    app = WalletApplication(qt_app, repository)
    qt_app.processEvents()
    yield app, repository
    app.close()


def child(app: WalletApplication, name: str) -> QObject:
    found = app.window.findChild(QObject, name)
    assert found is not None, f"QML object is missing: {name}"
    return found


def invoke(item: QObject, method: str) -> None:
    assert QMetaObject.invokeMethod(item, method, Qt.DirectConnection)


def set_text(app: WalletApplication, name: str, value: str) -> None:
    assert child(app, name).setProperty("text", value)


def fresh_password() -> str:
    return secrets.token_urlsafe(18)


def test_window_geometry_chrome_and_qml_load_cleanly(wallet_app) -> None:
    app, _repository = wallet_app

    assert app.controller.currentScreen == "welcome"
    assert app.window.title() == "Holon Wallet"
    assert (app.window.width(), app.window.height()) == (514, 686)
    assert (app.window.minimumWidth(), app.window.minimumHeight()) == (430, 575)
    assert app.window.flags() & Qt.FramelessWindowHint
    assert app.qml_warnings == []
    assert child(app, "windowDragArea")
    assert child(app, "minimizeButton")
    assert child(app, "closeButton")
    assert child(app, "createAccountButton").property("enabled")
    assert child(app, "importAccountButton").property("enabled")


def test_create_ui_persists_after_done_and_enables_wallet_controls(
    wallet_app, qt_app,
) -> None:
    app, repository = wallet_app
    password = fresh_password()

    invoke(child(app, "createAccountButton"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "password"
    assert not child(app, "passwordField").property("revealed")
    set_text(app, "passwordTextInput", password)
    set_text(app, "confirmPasswordTextInput", password)
    qt_app.processEvents()
    invoke(child(app, "passwordSubmitButton"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "backup"
    assert len(app.controller.backupWords) == 12
    assert not repository.exists
    invoke(child(app, "copySeedButton"), "trigger")
    copied = QGuiApplication.clipboard().text()
    assert len(copied.split()) == 12
    assert app.controller._clipboard_timer.isActive()

    invoke(child(app, "finishBackupButton"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "main"
    assert repository.exists
    assert app.controller.backupWords == []
    assert QGuiApplication.clipboard().text() == ""
    assert child(app, "sendAction").property("enabled")
    assert not child(app, "transactionsAction").property("enabled")
    assert child(app, "settingsAction").property("enabled")

    invoke(child(app, "settingsAction"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "wallets"
    assert not child(app, "searchCard").property("enabled")
    assert child(app, "addAccount").property("enabled")
    invoke(child(app, "addAccount"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "import"
    assert app.controller.importPrivateOnly
    assert child(app, "importPage").property("selectedType") == "private"
    invoke(child(app, "importBackButton"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "wallets"
    invoke(child(app, "backButton"), "trigger")
    assert app.controller.currentScreen == "main"


def test_import_navigation_and_cancel_clear_fields(wallet_app, qt_app) -> None:
    app, repository = wallet_app
    phrase = generate_mnemonic().value

    invoke(child(app, "importAccountButton"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "import"
    set_text(app, "seedPhraseInput", phrase)
    invoke(child(app, "importBackButton"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "welcome"
    assert child(app, "seedPhraseInput").property("text") == ""
    assert not repository.exists


def test_locked_restart_has_masked_password_and_generic_failure(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    record = repository.new_record(generate_mnemonic(), "Main Account")
    repository.create_new(password, record)
    app = WalletApplication(qt_app, repository)
    try:
        assert app.controller.currentScreen == "password"
        assert app.controller.passwordTitle == "Unlock Wallet"
        set_text(app, "passwordTextInput", fresh_password())
        invoke(child(app, "passwordSubmitButton"), "trigger")
        assert app.controller.errorMessage == "Authentication failed"
        set_text(app, "passwordTextInput", password)
        invoke(child(app, "passwordSubmitButton"), "trigger")
        assert app.controller.currentScreen == "main"
    finally:
        app.close()


def test_mock_action_review_password_result_and_cancel(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    repository.create_new(
        password, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    app = WalletApplication(qt_app, repository)
    try:
        set_text(app, "passwordTextInput", password)
        invoke(child(app, "passwordSubmitButton"), "trigger")
        invoke(child(app, "sendAction"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "mock_review"
        assert child(app, "mockNetwork").property("text") == "Base  ·  8453"
        assert child(app, "mockAmount").property("text") == "1 USDC"
        assert "not a real address" in child(app, "mockRecipient").property("text")
        assert "no RPC request" in child(app, "mockFee").property("text")
        assert child(app, "mockExpiry").property("text").endswith("UTC")

        invoke(child(app, "mockContinueButton"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "mock_password"
        assert not child(app, "mockPasswordField").property("revealed")
        assert not child(app, "mockAuthorizeButton").property("enabled")
        set_text(app, "mockPasswordTextInput", fresh_password())
        qt_app.processEvents()
        assert child(app, "mockAuthorizeButton").property("enabled")
        invoke(child(app, "mockAuthorizeButton"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "mock_result"
        assert child(app, "mockResultTitle").property("text") == "Authentication failed"
        assert "No transaction was signed or sent" in child(
            app, "mockResultMessage",
        ).property("text")
        invoke(child(app, "mockResultDoneButton"), "trigger")

        invoke(child(app, "sendAction"), "trigger")
        invoke(child(app, "mockContinueButton"), "trigger")
        set_text(app, "mockPasswordTextInput", password)
        invoke(child(app, "mockAuthorizeButton"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "mock_result"
        assert child(app, "mockResultTitle").property("text") == "Simulation authorized"
        assert app.controller.actionResultSuccess
        invoke(child(app, "mockResultDoneButton"), "trigger")

        invoke(child(app, "sendAction"), "trigger")
        invoke(child(app, "mockContinueButton"), "trigger")
        invoke(child(app, "mockCancelButton"), "trigger")
        assert app.controller.currentScreen == "main"

        invoke(child(app, "sendAction"), "trigger")
        invoke(child(app, "mockRejectButton"), "trigger")
        assert app.controller.currentScreen == "main"
    finally:
        app.close()


def test_malformed_existing_vault_is_not_replaced(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    atomic_write_json(repository.paths.vault, {"schema_version": 999})
    before = repository.paths.vault.read_bytes()
    app = WalletApplication(qt_app, repository)
    try:
        assert app.controller.currentScreen == "unavailable"
        assert child(app, "retryWalletButton").property("enabled")
        assert repository.paths.vault.read_bytes() == before
    finally:
        app.close()


def test_resize_close_and_idle_first_run_create_no_files(wallet_app, qt_app) -> None:
    app, repository = wallet_app
    app.window.resize(430, 575)
    qt_app.processEvents()

    assert (app.window.width(), app.window.height()) == (430, 575)
    assert list(repository.paths.data_dir.iterdir()) == []
