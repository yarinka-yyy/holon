from __future__ import annotations

import hashlib
import os
import secrets

os.environ.setdefault("QT_PREFERRED_PHYSICAL_DEVICE", "cpu")
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("QT_QUICK_BACKEND", "software")

import pytest
from PySide6.QtCore import QObject, QMetaObject, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtTest import QTest

from holon_wallet.application import WalletApplication
from holon_wallet.approval import UINT256_MAX
from holon_wallet.history import HistoryStatus, HistoryStore, WalletHistoryRecord
from holon_wallet.storage import WalletPaths, atomic_write_json
from holon_wallet.transfer import TransferPreflightCode, TransferPreflightError
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic, import_private_key
from wallet_public_support import (
    DeferredExecutor,
    ImmediateExecutor,
    StubPublicDataService,
    StubPriceService,
    StubTransferPreflightService,
    mainnet_services,
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


def fill_send(
    app: WalletApplication,
    recipient: str,
    amount: str = "1",
    network: str = "base",
    asset: str = "usdc",
) -> None:
    invoke(
        child(app, "sendEthereumNetwork" if network == "ethereum" else "sendBaseNetwork"),
        "trigger",
    )
    invoke(child(app, "sendEthAsset" if asset == "eth" else "sendUsdcAsset"), "trigger")
    set_text(app, "transferRecipientInput", recipient)
    set_text(app, "transferAmountInput", amount)


def fresh_password() -> str:
    return secrets.token_urlsafe(18)


def make_app(
    qt_app: QGuiApplication,
    repository: VaultRepository,
    public_data_service: StubPublicDataService | None = None,
    transfer_preflight_service: StubTransferPreflightService | None = None,
    transfer_executor=None,
    mainnet_enabled: bool = True,
    revoke_enabled: bool = True,
) -> WalletApplication:
    history = HistoryStore(repository.paths)
    mainnet, tracker, rpc = mainnet_services(
        repository, history, enabled=mainnet_enabled,
        revoke_enabled=revoke_enabled,
    )
    app = WalletApplication(
        qt_app=qt_app,
        repository=repository,
        public_data_service=public_data_service or StubPublicDataService(),
        history_store=history,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=(
            transfer_preflight_service or StubTransferPreflightService()
        ),
        transfer_executor=transfer_executor or ImmediateExecutor(),
        mainnet_executor=mainnet,
        receipt_tracker=tracker,
        receipt_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    app._test_mainnet_rpc = rpc
    return app


def test_token_approvals_v2_review_confirm_submit_and_policy_gate(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path / "enabled"))
    app = make_app(qt_app, repository)
    secret = fresh_password()
    try:
        invoke(child(app, "createAccountButton"), "trigger")
        set_text(app, "passwordTextInput", secret)
        set_text(app, "confirmPasswordTextInput", secret)
        invoke(child(app, "passwordSubmitButton"), "trigger")
        invoke(child(app, "finishBackupButton"), "trigger")
        qt_app.processEvents()
        invoke(child(app, "settingsAction"), "trigger")
        QTest.qWait(220)
        invoke(child(app, "settingsSecurity"), "trigger")
        QTest.qWait(220)
        assert child(app, "settingsTokenApprovals").property("visible")
        invoke(child(app, "settingsTokenApprovals"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "approvals"
        assert child(app, "approvalList").property("count") == 2
        assert app.controller.approvalRecords[1]["revokeAvailable"]
        app._test_mainnet_rpc.allowance_value = UINT256_MAX
        assert app.controller.refreshApprovals()
        assert app.controller.approvalRecords[1]["allowance"] == "Unlimited USDC"

        assert app.controller.prepareRevoke("base")
        qt_app.processEvents()
        assert app.controller.currentScreen == "revoke_review"
        assert app.controller.revokeAction["networkId"] == "base"
        invoke(child(app, "continueRevokeButton"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "revoke_confirm"
        assert not child(app, "revokePasswordField").property("revealed")
        set_text(app, "revokePasswordInput", secret)
        invoke(child(app, "revokeConfirmationCheckbox"), "trigger")
        invoke(child(app, "revokeSubmitButton"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "revoke_result"
        assert app.controller.mainnetResult["actionType"] == "revoke"
        assert app._test_mainnet_rpc.send_calls == 1
        assert child(app, "revokeResultTitle").property("text")
    finally:
        app.close()

    disabled_repository = VaultRepository(WalletPaths(tmp_path / "disabled"))
    disabled = make_app(
        qt_app, disabled_repository, revoke_enabled=False,
    )
    try:
        invoke(child(disabled, "createAccountButton"), "trigger")
        set_text(disabled, "passwordTextInput", secret)
        set_text(disabled, "confirmPasswordTextInput", secret)
        invoke(child(disabled, "passwordSubmitButton"), "trigger")
        invoke(child(disabled, "finishBackupButton"), "trigger")
        qt_app.processEvents()
        invoke(child(disabled, "settingsAction"), "trigger")
        QTest.qWait(220)
        invoke(child(disabled, "settingsSecurity"), "trigger")
        QTest.qWait(220)
        invoke(child(disabled, "settingsTokenApprovals"), "trigger")
        qt_app.processEvents()
        assert not disabled.controller.approvalRecords[1]["revokeAvailable"]
        assert disabled.controller.approvalRecords[1]["status"] == "LIVE"
        disabled._test_mainnet_rpc.allowance_value = 0
        assert disabled.controller.refreshApprovals()
        assert disabled.controller.approvalRecords[1]["status"] == "NO_ACTIVE_ALLOWANCE"
    finally:
        disabled.close()


def test_window_geometry_chrome_and_qml_load_cleanly(wallet_app) -> None:
    app, _repository = wallet_app

    assert app.controller.currentScreen == "welcome"
    assert app.window.title() == "Holon Wallet"
    assert (app.window.width(), app.window.height()) == (430, 703)
    assert (app.window.minimumWidth(), app.window.minimumHeight()) == (430, 703)
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
    assert app.controller.currentScreen == "settings"
    assert child(app, "settingsAccounts").property("enabled")
    assert child(app, "settingsNetworkData").property("enabled")
    assert child(app, "settingsSecurity").property("enabled")
    assert child(app, "settingsAbout").property("enabled")
    invoke(child(app, "settingsAccounts"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "wallets"
    assert app.window.findChild(QObject, "searchCard") is None
    assert child(app, "addAccount").property("enabled")
    invoke(child(app, "addAccount"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "import"
    assert app.controller.importPrivateOnly
    assert child(app, "importPage").property("selectedType") == "private"
    invoke(child(app, "importBackButton"), "trigger")
    qt_app.processEvents()
    assert app.controller.currentScreen == "wallets"
    invoke(child(app, "accountsBackButton"), "trigger")
    assert app.controller.currentScreen == "settings"
    invoke(child(app, "settingsBackButton"), "trigger")
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


def test_existing_vault_opens_public_main_and_refreshes_without_password(
    tmp_path, qt_app,
) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    record = repository.new_record(generate_mnemonic(), "Main Account")
    repository.create_new(password, record)
    second = repository.new_record(
        import_private_key(secrets.token_hex(32)), "Account 2",
    )
    repository.append(password, second)
    service = StubPublicDataService()
    app = make_app(qt_app, repository, service)
    try:
        assert app.controller.currentScreen == "main"
        assert app.controller.activeProfile["address"] == record.summary.address
        assert service.calls[-1][2] == ("ethereum", "base")
        assert child(app, "signingLockedChip").property("visible")
        assert child(app, "signingLockedChipLabel").property("text") == (
            "Signing locked"
        )
        assert app.controller.ethereumData["ethValue"] == "1 ETH"
        assert app.controller.baseData["usdcValue"] == "2.5 USDC"
        assert len(app.controller.profiles) == 2
        assert app.controller.selectProfile(second.summary.profile_id)
        qt_app.processEvents()
        assert app.controller.currentScreen == "main"
        assert app.controller.activeProfileId == second.summary.profile_id
        assert service.calls[-1][0] == second.summary.profile_id
        assert service.calls[-1][2] == ("ethereum", "base")
    finally:
        app.close()


def test_protected_recovery_review_confirm_reveal_and_clipboard(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    material = generate_mnemonic()
    repository.create_new(
        password, repository.new_record(material, "Main Account"),
    )
    vault_before = repository.paths.vault.read_bytes()
    app = make_app(qt_app, repository)
    try:
        invoke(child(app, "settingsAction"), "trigger")
        invoke(child(app, "settingsSecurity"), "trigger")
        assert app.controller.currentScreen == "settings_info"
        assert child(app, "settingsRecoveryMaterial").property("enabled")
        invoke(child(app, "settingsRecoveryMaterial"), "trigger")
        QTest.qWait(220)
        qt_app.processEvents()
        assert app.controller.currentScreen == "recovery_review"
        assert app.controller.recoverySelection == "seed_phrase"
        assert child(app, "recoverySeedChoice").property("visible")

        invoke(child(app, "recoveryPrivateKeyChoice"), "trigger")
        assert app.controller.recoverySelection == "private_key"
        invoke(child(app, "recoverySeedChoice"), "trigger")
        assert app.controller.recoverySelection == "seed_phrase"
        invoke(child(app, "recoveryReviewContinue"), "trigger")
        assert app.controller.currentScreen == "recovery_confirm"
        assert not child(app, "recoveryPasswordField").property("revealed")
        first_id = app.controller.recoveryAction["actionId"]

        set_text(app, "recoveryPasswordInput", fresh_password())
        invoke(child(app, "recoveryConfirmCheckbox"), "trigger")
        invoke(child(app, "recoveryRevealButton"), "trigger")
        assert app.controller.currentScreen == "recovery_review"
        assert app.controller.recoveryAction == {}
        assert "Authentication failed" in app.controller.errorMessage

        invoke(child(app, "recoveryReviewContinue"), "trigger")
        assert app.controller.recoveryAction["actionId"] != first_id
        set_text(app, "recoveryPasswordInput", password)
        invoke(child(app, "recoveryConfirmCheckbox"), "trigger")
        invoke(child(app, "recoveryRevealButton"), "trigger")
        assert app.controller.currentScreen == "recovery_reveal"
        secret_display = child(app, "recoverySecretDisplay")
        assert secret_display.property("text") is None
        assert secret_display.dynamicPropertyNames() == []
        assert app.controller.recoveryRevealSeconds == 60

        invoke(child(app, "copyRecoveryButton"), "trigger")
        copied = QGuiApplication.clipboard().text()
        assert hashlib.sha256(copied.encode()).digest() == hashlib.sha256(
            material.value.encode(),
        ).digest()
        del copied
        assert app.controller.recoveryCopyUsed
        assert app.controller.recoveryClipboardSeconds == 30
        assert not app.controller.copyRecoveryMaterial()

        app.controller.handleWindowActiveChanged(False)
        assert app.controller.currentScreen == "settings_info"
        assert app.controller.settingsSection == "security"
        assert not secret_display.has_material()
        assert repository.paths.vault.read_bytes() == vault_before
        assert app.controller.historyRecords == []
        clipboard_value = QGuiApplication.clipboard().text()
        assert hashlib.sha256(clipboard_value.encode()).digest() == hashlib.sha256(
            material.value.encode(),
        ).digest()
        del clipboard_value

        QGuiApplication.clipboard().setText("replacement")
        app.controller._clear_recovery_clipboard()
        assert QGuiApplication.clipboard().text() == "replacement"
        QGuiApplication.clipboard().setText("timer fixture")
        app.controller._recovery_clipboard_digest = hashlib.sha256(
            b"timer fixture",
        ).digest()
        app.controller._recovery_clipboard_seconds = 1
        app.controller._tick_recovery_clipboard()
        assert QGuiApplication.clipboard().text() == ""
        QGuiApplication.clipboard().clear()
    finally:
        app.close()


def test_send_review_mainnet_confirmation_result_and_history(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    repository.create_new(
        password, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    app = make_app(qt_app, repository)
    try:
        invoke(child(app, "sendAction"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "send"
        assert not child(app, "prepareTransferButton").property("enabled")
        assert not child(app, "sendEthereumNetwork").property("selected")
        assert not child(app, "sendBaseNetwork").property("selected")
        assert not child(app, "sendEthAsset").property("selected")
        assert not child(app, "sendUsdcAsset").property("selected")
        invoke(child(app, "sendEthereumNetwork"), "trigger")
        qt_app.processEvents()
        assert child(app, "sendEthereumNetwork").property("selected")
        assert child(app, "assetSelectorButton").property("enabled")
        invoke(child(app, "assetSelectorButton"), "trigger")
        qt_app.processEvents()
        assert child(app, "assetSelectorButton").property("menuOpen")
        invoke(child(app, "sendEthAsset"), "trigger")
        assert not child(app, "assetSelectorButton").property("menuOpen")
        set_text(app, "transferAmountInput", "0.1")
        invoke(child(app, "sendBaseNetwork"), "trigger")
        assert child(app, "transferAmountInput").property("text") == ""
        assert not child(app, "sendEthAsset").property("selected")
        recipient = "0x" + "44" * 20
        QGuiApplication.clipboard().setText(recipient)
        invoke(child(app, "pasteRecipientButton"), "trigger")
        invoke(child(app, "sendBaseNetwork"), "trigger")
        invoke(child(app, "sendUsdcAsset"), "trigger")
        assert child(app, "maxTransferButton").property("enabled")
        invoke(child(app, "maxTransferButton"), "trigger")
        assert child(app, "transferAmountInput").property("text") == "2.5"
        set_text(app, "transferAmountInput", "1,0")
        qt_app.processEvents()
        assert child(app, "transferRecipientInput").property("text") == recipient
        assert child(app, "prepareTransferButton").property("enabled")
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "transfer_review"
        assert child(app, "transferReviewPageProgress").property("activeStep") == 0
        assert child(app, "transferReviewNetwork").property("text") == "Base · 8453"
        assert child(app, "transferReviewAmount").property("text") == "1 USDC"
        assert child(app, "transferReviewRecipient").property("enabled")
        assert app.controller.transferAction["recipient"].endswith("444444")
        assert child(app, "transferReviewFee").property("secondary").endswith("ETH")
        assert app.controller.transferAction["expiresAt"].endswith("UTC")
        assert app.controller.mainnetFeeLimit.endswith("ETH")
        invoke(child(app, "transferDetailsButton"), "trigger")
        assert child(app, "transferReviewScroll").property("contentHeight") == 1090

        invoke(child(app, "editTransferButton"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "send"
        assert child(app, "transferRecipientInput").property("text") == recipient
        assert child(app, "transferAmountInput").property("text") == "1"
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()
        assert child(app, "continueMainnetButton").property("enabled")
        invoke(child(app, "continueMainnetButton"), "trigger")
        assert app.controller.currentScreen == "sign_transfer"
        assert child(app, "mainnetSignPageProgress").property("activeStep") == 1
        assert not child(app, "mainnetPasswordField").property("revealed")
        assert not child(app, "mainnetSendButton").property("enabled")
        set_text(app, "mainnetPasswordInput", password)
        qt_app.processEvents()
        assert not child(app, "mainnetSendButton").property("enabled")
        invoke(child(app, "mainnetConfirmationCheckbox"), "trigger")
        qt_app.processEvents()
        assert child(app, "mainnetSendButton").property("enabled")
        invoke(child(app, "mainnetSendButton"), "trigger")
        qt_app.processEvents()
        assert child(app, "mainnetPasswordInput").property("text") == ""
        assert app.controller.currentScreen == "transfer_result"
        assert child(app, "mainnetResultPageProgress").property("activeStep") == 3
        assert child(app, "mainnetResultTitle").property("text") == (
            "Transaction submitted"
        )
        assert child(app, "mainnetRecoveredSigner").property("text") == (
            app.controller.activeProfile["address"]
        )
        assert child(app, "mainnetTransactionHash").property("text").startswith("0x")
        assert child(app, "mainnetPublicStatus").property("text") == "Pending"
        assert app.controller.mainnetResult["canCheckStatus"]
        invoke(child(app, "checkMainnetStatusButton"), "trigger")
        qt_app.processEvents()
        assert child(app, "mainnetPublicStatus").property("text") == "Pending"
        invoke(child(app, "mainnetResultDoneButton"), "trigger")
        assert app.controller.currentScreen == "main"
        assert len(app.controller.historyRecords) == 2
        assert all(not item["simulated"] for item in app.controller.historyRecords)
        assert {item["status"] for item in app.controller.historyRecords} == {
            "prepared", "pending",
        }
        assert sum(bool(item["transactionHash"]) for item in app.controller.historyRecords) == 1
        assert app._test_mainnet_rpc.send_calls == 1
        invoke(child(app, "transactionsAction"), "trigger")
        qt_app.processEvents()
        assert child(app, "historyList").property("count") == 2
        QGuiApplication.clipboard().clear()
    finally:
        app.close()


def test_mainnet_runtime_gate_wrong_password_and_cancel(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    repository.create_new(
        password, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    app = make_app(qt_app, repository, mainnet_enabled=False)
    try:
        invoke(child(app, "sendAction"), "trigger")
        fill_send(app, "0x" + "44" * 20)
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "transfer_review"
        assert not child(app, "continueMainnetButton").property("enabled")
        assert app.controller.mainnetFeeLimit == "Not configured"
    finally:
        app.close()

    active = make_app(qt_app, repository)
    try:
        invoke(child(active, "sendAction"), "trigger")
        fill_send(active, "0x" + "55" * 20)
        invoke(child(active, "prepareTransferButton"), "trigger")
        invoke(child(active, "continueMainnetButton"), "trigger")
        invoke(child(active, "mainnetCancelButton"), "trigger")
        assert active.controller.currentScreen == "main"

        invoke(child(active, "sendAction"), "trigger")
        fill_send(active, "0x" + "66" * 20)
        invoke(child(active, "prepareTransferButton"), "trigger")
        invoke(child(active, "continueMainnetButton"), "trigger")
        set_text(active, "mainnetPasswordInput", fresh_password())
        invoke(child(active, "mainnetConfirmationCheckbox"), "trigger")
        invoke(child(active, "mainnetSendButton"), "trigger")
        qt_app.processEvents()
        assert active.controller.currentScreen == "transfer_result"
        assert child(active, "mainnetResultTitle").property("text") == (
            "Authentication failed"
        )
        assert not child(active, "mainnetProofCard").property("visible")
        invoke(child(active, "mainnetResultDoneButton"), "trigger")
        assert active.controller.currentScreen == "main"
    finally:
        active.close()


def test_ordinary_window_close_is_blocked_only_during_submission(
    tmp_path, qt_app,
) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    repository.create_new(
        password, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    deferred = DeferredExecutor()
    app = make_app(qt_app, repository, transfer_executor=deferred)
    try:
        invoke(child(app, "sendAction"), "trigger")
        fill_send(app, "0x" + "44" * 20)
        invoke(child(app, "prepareTransferButton"), "trigger")
        deferred.run_next()
        invoke(child(app, "continueMainnetButton"), "trigger")
        set_text(app, "mainnetPasswordInput", password)
        invoke(child(app, "mainnetConfirmationCheckbox"), "trigger")
        invoke(child(app, "mainnetSendButton"), "trigger")
        assert app.controller.mainnetExecutionInProgress
        assert app.controller.currentScreen == "submit_transfer"
        assert child(app, "submitPageProgress").property("activeStep") == 2

        app.window.close()
        qt_app.processEvents()
        assert app.window.isVisible()

        deferred.run_next()
        qt_app.processEvents()
        assert app.controller.currentScreen == "transfer_result"
        assert app.controller.canCloseWallet
        app.window.close()
        qt_app.processEvents()
        assert not app.window.isVisible()
    finally:
        app.close()


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
        invoke(child(app, "sendAction"), "trigger")
        fill_send(app, "0x" + "44" * 20)
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "send"
        assert child(app, "transferErrorLabel").property("text") == (
            "Insufficient USDC for this transfer"
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
        invoke(child(app, "sendAction"), "trigger")
        fill_send(app, "0x" + "44" * 20)
        invoke(child(app, "prepareTransferButton"), "trigger")
        qt_app.processEvents()

        assert app.controller.currentScreen == "send"
        assert app.controller.transferPreparing
        assert not child(app, "prepareTransferButton").property("enabled")
        invoke(child(app, "sendBackButton"), "trigger")
        executor.run_next()
        qt_app.processEvents()

        assert app.controller.currentScreen == "main"
        assert app.controller.transferAction == {}
        assert app.controller.historyRecords == []
    finally:
        app.close()


def test_v2_receive_settings_privacy_and_transaction_details(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = fresh_password()
    repository.create_new(
        password, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    app = make_app(qt_app, repository)
    try:
        assert "Inter" in app.font_family

        assert app.controller.balancesVisible
        invoke(child(app, "balanceEyeButton"), "trigger")
        assert not app.controller.balancesVisible
        address_text = child(app, "accountAddressText")
        copy_button = child(app, "accountCopyButton")
        receive_zone = child(app, "accountReceiveZone")
        assert copy_button.property("x") == pytest.approx(
            address_text.property("x") + address_text.property("width") + 8,
        )
        assert receive_zone.property("width") <= copy_button.property("x")
        invoke(child(app, "accountCopyButton"), "trigger")
        QTest.qWait(220)
        assert QGuiApplication.clipboard().text() == app.controller.activeProfile["address"]
        assert child(app, "accountCopiedFeedback").property("visible")
        QTest.qWait(2_050)
        assert not child(app, "accountCopiedFeedback").property("visible")
        invoke(child(app, "accountReceiveZone"), "trigger")
        qt_app.processEvents()
        assert app.controller.currentScreen == "receive"
        assert child(app, "receiveAddress").property("text") == (
            app.controller.activeProfile["address"]
        )
        invoke(child(app, "receiveEthereum"), "trigger")
        assert app.controller.receiveNetwork == "ethereum"
        invoke(child(app, "copyReceiveAddress"), "trigger")
        QTest.qWait(50)
        assert QGuiApplication.clipboard().text() == app.controller.activeProfile["address"]
        assert child(app, "receiveCopiedFeedback").property("visible")
        invoke(child(app, "receiveBackButton"), "trigger")
        assert app.controller.currentScreen == "main"

        invoke(child(app, "settingsAction"), "trigger")
        invoke(child(app, "settingsNetworkData"), "trigger")
        assert app.controller.currentScreen == "settings_info"
        assert app.controller.settingsSection == "network"
        invoke(child(app, "settingsInfoBackButton"), "trigger")
        invoke(child(app, "settingsSecurity"), "trigger")
        assert app.controller.settingsSection == "security"
        invoke(child(app, "settingsInfoBackButton"), "trigger")
        invoke(child(app, "settingsAbout"), "trigger")
        assert app.controller.settingsSection == "about"
        invoke(child(app, "settingsInfoBackButton"), "trigger")
        invoke(child(app, "settingsBackButton"), "trigger")

        invoke(child(app, "sendAction"), "trigger")
        fill_send(app, "0x" + "44" * 20)
        invoke(child(app, "prepareTransferButton"), "trigger")
        action_id = app.controller.transferAction["actionId"]
        app.controller.cancelTransfer()
        app.controller.showHistory()
        assert app.controller.showTransactionDetails(action_id)
        qt_app.processEvents()
        assert app.controller.currentScreen == "transaction_details"
        assert child(app, "transactionDetailsPage").property("enabled")
        assert app.controller.selectedHistoryRecord["maxTotalFeeWei"]
        invoke(child(app, "transactionDetailsBackButton"), "trigger")
        assert app.controller.currentScreen == "history"
        assert app.qml_warnings == []
    finally:
        QGuiApplication.clipboard().clear()
        app.close()


def test_malformed_existing_vault_is_not_replaced(tmp_path, qt_app) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    atomic_write_json(repository.paths.vault, {"schema_version": 999})
    before = repository.paths.vault.read_bytes()
    app = make_app(qt_app, repository)
    try:
        assert app.controller.currentScreen == "unavailable"
        assert child(app, "retryUnavailableButton").property("enabled")
        assert repository.paths.vault.read_bytes() == before
    finally:
        app.close()


def test_resize_close_and_idle_first_run_create_no_files(wallet_app, qt_app) -> None:
    app, repository = wallet_app
    app.window.resize(430, 703)
    qt_app.processEvents()

    assert (app.window.width(), app.window.height()) == (430, 703)
    assert list(repository.paths.data_dir.iterdir()) == []


def test_guard_banner_is_public_timed_and_never_navigates(wallet_app, qt_app) -> None:
    app, _ = wallet_app
    banner = child(app, "guardOpenBanner")
    for screen in ("welcome", "password", "main", "transfer_review"):
        app.controller._set_screen(screen)
        app.controller.showGuardOpenNotice()
        qt_app.processEvents()
        assert app.controller.currentScreen == screen
        assert banner.property("visible")
        assert app.controller.guardOpenNotice == (
            "Opened by Guard · no Guard action authorized"
        )
        assert app.controller._guard_notice_timer.interval() == 6_000
        app.controller._clear_guard_notice()
        qt_app.processEvents()
        assert not banner.property("visible")


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
        assert app.controller.selectedNetwork == "all"
        assert service.calls[-1][2] == ("ethereum", "base")
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
