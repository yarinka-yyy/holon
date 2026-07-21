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
from holon_wallet.history import HistoryStatus, HistoryStore, WalletHistoryRecord
from holon_wallet.signer import OfflineSigningPolicy, OfflineTransferSigner
from holon_wallet.storage import WalletPaths, atomic_write_json
from holon_wallet.transfer import TransferPreflightCode, TransferPreflightError
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic
from wallet_public_support import (
    DeferredExecutor,
    ImmediateExecutor,
    StubPublicDataService,
    StubTransferPreflightService,
)


@pytest.fixture(scope="module")
def qt_app() -> QGuiApplication:
    return QGuiApplication.instance() or QGuiApplication([])


@pytest.fixture
def wallet_app(tmp_path, qt_app: QGuiApplication):
    repository = VaultRepository(WalletPaths(tmp_path))
    app = make_app(qt_app, repository)
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


def make_app(
    qt_app: QGuiApplication,
    repository: VaultRepository,
    public_data_service: StubPublicDataService | None = None,
    transfer_preflight_service: StubTransferPreflightService | None = None,
    transfer_executor=None,
    offline_signer: OfflineTransferSigner | None = None,
) -> WalletApplication:
    return WalletApplication(
        qt_app,
        repository,
        public_data_service or StubPublicDataService(),
        HistoryStore(repository.paths),
        ImmediateExecutor(),
        transfer_preflight_service or StubTransferPreflightService(),
        transfer_executor or ImmediateExecutor(),
        offline_signer or OfflineTransferSigner(
            repository, OfflineSigningPolicy(10**18),
        ),
    )


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
    assert child(app, "transactionsAction").property("enabled")
    assert child(app, "settingsAction").property("enabled")
    assert child(app, "allNetworksCard").property("enabled")
    assert child(app, "ethereumNetworkCard").property("enabled")
    assert child(app, "baseNetworkCard").property("enabled")
    assert app.controller.publicDataBanner == "LOCAL WALLET  ·  LIVE PUBLIC DATA"

    invoke(child(app, "transactionsAction"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "history"
    assert child(app, "historyStateLabel").property("text") == (
        "No Wallet-initiated transactions yet"
    )
    invoke(child(app, "historyBackButton"), "trigger")
    assert app.controller.currentScreen == "main"

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
    app = make_app(qt_app, repository)
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


def test_send_review_offline_sign_result_and_history(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    repository.create_new(
        password, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    app = make_app(qt_app, repository)
    try:
        set_text(app, "passwordTextInput", password)
        invoke(child(app, "passwordSubmitButton"), "trigger")
        invoke(child(app, "sendAction"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "send"
        assert not child(app, "prepareTransferButton").property("enabled")
        recipient = "0x" + "44" * 20
        QGuiApplication.clipboard().setText(recipient)
        invoke(child(app, "pasteRecipientButton"), "trigger")
        qt_app.processEvents()
        assert child(app, "transferRecipientInput").property("text") == recipient
        assert child(app, "prepareTransferButton").property("enabled")
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "transfer_review"
        assert child(app, "transferReviewNetwork").property("text") == "Base  ·  8453"
        assert child(app, "transferReviewAmount").property("text") == "1 USDC"
        assert child(app, "transferReviewRecipient").property("text").endswith("444444")
        assert child(app, "transferReviewFee").property("text").endswith("ETH")
        assert child(app, "transferReviewExpiry").property("text").endswith("UTC")
        assert child(app, "offlineSigningLimit").property("text").endswith("ETH")
        invoke(child(app, "transferDetailsButton"), "trigger")
        assert child(app, "transferReviewScroll").property("contentHeight") == 760

        invoke(child(app, "editTransferButton"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "send"
        assert child(app, "transferRecipientInput").property("text") == recipient
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()
        assert child(app, "continueOfflineSigningButton").property("enabled")
        invoke(child(app, "continueOfflineSigningButton"), "trigger")
        assert app.controller.currentScreen == "sign_transfer"
        assert not child(app, "offlineSigningPasswordField").property("revealed")
        assert not child(app, "offlineSignButton").property("enabled")
        set_text(app, "offlineSigningPasswordInput", password)
        qt_app.processEvents()
        assert child(app, "offlineSignButton").property("enabled")
        invoke(child(app, "offlineSignButton"), "trigger")
        qt_app.processEvents()
        assert child(app, "offlineSigningPasswordInput").property("text") == ""
        assert app.controller.currentScreen == "sign_result"
        assert child(app, "offlineResultTitle").property("text") == (
            "Transaction signed locally"
        )
        assert child(app, "offlineRecoveredSigner").property("text") == (
            app.controller.activeProfile["address"]
        )
        assert child(app, "offlineTransactionHash").property("text").startswith("0x")
        invoke(child(app, "offlineResultDoneButton"), "trigger")
        assert app.controller.currentScreen == "main"
        assert len(app.controller.historyRecords) == 2
        assert all(not item["simulated"] for item in app.controller.historyRecords)
        assert all(item["status"] == "prepared" for item in app.controller.historyRecords)
        assert all(item["transactionHash"] == "" for item in app.controller.historyRecords)
        QGuiApplication.clipboard().clear()
    finally:
        app.close()


def test_offline_signing_fee_gate_wrong_password_and_cancel(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    repository.create_new(
        password, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    unavailable = OfflineTransferSigner(repository, OfflineSigningPolicy(None))
    app = make_app(qt_app, repository, offline_signer=unavailable)
    try:
        set_text(app, "passwordTextInput", password)
        invoke(child(app, "passwordSubmitButton"), "trigger")
        invoke(child(app, "sendAction"), "trigger")
        set_text(app, "transferRecipientInput", "0x" + "44" * 20)
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "transfer_review"
        assert not child(app, "continueOfflineSigningButton").property("enabled")
        assert child(app, "offlineSigningLimit").property("text") == "Not configured"
    finally:
        app.close()

    active = make_app(qt_app, repository)
    try:
        set_text(active, "passwordTextInput", password)
        invoke(child(active, "passwordSubmitButton"), "trigger")
        invoke(child(active, "sendAction"), "trigger")
        set_text(active, "transferRecipientInput", "0x" + "55" * 20)
        invoke(child(active, "prepareTransferButton"), "trigger")
        invoke(child(active, "continueOfflineSigningButton"), "trigger")
        invoke(child(active, "offlineSignCancelButton"), "trigger")
        assert active.controller.currentScreen == "main"

        invoke(child(active, "sendAction"), "trigger")
        set_text(active, "transferRecipientInput", "0x" + "66" * 20)
        invoke(child(active, "prepareTransferButton"), "trigger")
        invoke(child(active, "continueOfflineSigningButton"), "trigger")
        set_text(active, "offlineSigningPasswordInput", fresh_password())
        invoke(child(active, "offlineSignButton"), "trigger")
        qt_app.processEvents()
        assert active.controller.currentScreen == "sign_result"
        assert child(active, "offlineResultTitle").property("text") == (
            "Authentication failed"
        )
        assert not child(active, "offlineProofCard").property("visible")
        invoke(child(active, "offlineResultDoneButton"), "trigger")
        assert active.controller.currentScreen == "main"
    finally:
        active.close()


def test_send_failure_remains_on_form_and_writes_no_history(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    repository.create_new(
        password, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    failure = StubTransferPreflightService(
        TransferPreflightError(TransferPreflightCode.INSUFFICIENT_USDC),
    )
    app = make_app(qt_app, repository, transfer_preflight_service=failure)
    try:
        set_text(app, "passwordTextInput", password)
        invoke(child(app, "passwordSubmitButton"), "trigger")
        invoke(child(app, "sendAction"), "trigger")
        set_text(app, "transferRecipientInput", "0x" + "44" * 20)
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "send"
        assert child(app, "transferErrorLabel").property("text") == (
            "This Account does not have 1 USDC on Base"
        )
        assert app.controller.historyRecords == []
    finally:
        app.close()


def test_send_loading_back_ignores_late_result(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    repository.create_new(
        password, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    executor = DeferredExecutor()
    app = make_app(qt_app, repository, transfer_executor=executor)
    try:
        set_text(app, "passwordTextInput", password)
        invoke(child(app, "passwordSubmitButton"), "trigger")
        invoke(child(app, "sendAction"), "trigger")
        set_text(app, "transferRecipientInput", "0x" + "44" * 20)
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "send"
        assert app.controller.transferPreparing
        assert not child(app, "prepareTransferButton").property("visible")
        invoke(child(app, "sendBackButton"), "trigger")
        executor.run_next()
        qt_app.processEvents()

        assert app.controller.currentScreen == "main"
        assert app.controller.transferAction == {}
        assert app.controller.historyRecords == []
    finally:
        app.close()


def test_malformed_existing_vault_is_not_replaced(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    atomic_write_json(repository.paths.vault, {"schema_version": 999})
    before = repository.paths.vault.read_bytes()
    app = make_app(qt_app, repository)
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


def test_network_filters_refresh_and_history_record_render(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    profile = repository.new_record(generate_mnemonic(), "Main Account")
    repository.create_new(password, profile)
    HistoryStore(repository.paths).append(
        WalletHistoryRecord(
            action_id="act-ui-history",
            profile_id=profile.summary.profile_id,
            action_type="transfer",
            network="base",
            chain_id=8453,
            sender=profile.summary.address,
            recipient="0x" + "44" * 20,
            contract=None,
            token="USDC",
            amount_atomic="1000000",
            decimals=6,
            transaction_hash=None,
            status=HistoryStatus.PREPARED,
            created_at="2026-07-20T12:00:00Z",
            updated_at="2026-07-20T12:00:00Z",
            simulated=True,
        ),
    )
    service = StubPublicDataService()
    app = make_app(qt_app, repository, service)
    try:
        set_text(app, "passwordTextInput", password)
        invoke(child(app, "passwordSubmitButton"), "trigger")
        assert app.controller.selectedNetwork == "all"
        assert app.controller.ethereumData["ethValue"] == "1 ETH"
        assert app.controller.baseData["usdcValue"] == "2.5 USDC"

        invoke(child(app, "baseNetworkCard"), "trigger")
        assert app.controller.selectedNetwork == "base"
        assert service.calls[-1][2] == ("base",)
        invoke(child(app, "refreshButton"), "trigger")
        assert service.calls[-1][2] == ("base",)

        invoke(child(app, "transactionsAction"), "trigger")
        qt_app.processEvents()
        assert child(app, "historyList").property("count") == 1
        assert app.controller.historyRecords[0]["simulated"] is True
        invoke(child(app, "historyBackButton"), "trigger")
        assert app.controller.currentScreen == "main"
    finally:
        app.close()
