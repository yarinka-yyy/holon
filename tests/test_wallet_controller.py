from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QLocale, QTime

from holon_wallet.broadcast import (
    TRANSFER_EVENT_TOPIC,
    MainnetBroadcastPolicy,
    MainnetTransferCode,
    MainnetTransferExecutor,
)
from holon_wallet.controller import WalletController, _display_local_time
from holon_wallet.history import HistoryStatus, HistoryStore
from holon_wallet.signer import OfflineSigningPolicy
from holon_wallet.storage import StorageError, WalletPaths
from holon_wallet.transfer import (
    TransferFlowState,
    TransferPreflightCode,
    TransferPreflightError,
    format_atomic_amount,
)
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic, import_private_key
from wallet_public_support import (
    DeferredExecutor,
    ImmediateExecutor,
    StubPublicDataService,
    StubPriceService,
    StubTransferPreflightService,
    mainnet_services,
    public_snapshot,
)


def test_public_data_timestamp_uses_system_local_time_format() -> None:
    timestamp = "2026-07-22T07:05:00Z"
    local = datetime.fromisoformat("2026-07-22T07:05:00+00:00").astimezone()
    expected = QLocale.system().toString(
        QTime(local.hour, local.minute), QLocale.FormatType.ShortFormat,
    )

    assert _display_local_time(timestamp) == expected
    assert "UTC" not in _display_local_time(timestamp)


def password() -> str:
    return secrets.token_urlsafe(18)


def raw_private_key() -> str:
    while True:
        candidate = secrets.token_hex(32)
        try:
            import_private_key(candidate)
            return candidate
        except ValueError:
            continue


def controller(tmp_path) -> WalletController:
    repository = VaultRepository(WalletPaths(tmp_path))
    history = HistoryStore(repository.paths)
    mainnet, tracker, rpc = mainnet_services(repository, history)
    item = WalletController(
        repository,
        StubPublicDataService(),
        history,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        mainnet_executor=mainnet,
        receipt_tracker=tracker,
        receipt_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    item._test_mainnet_rpc = rpc
    return item


def test_create_persists_only_after_backup_acknowledgement(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()

    assert item.currentScreen == "welcome"
    item.beginCreate()
    assert item.currentScreen == "password"
    assert item.passwordConfirmRequired
    assert not item.submitPassword(secret, secret + "x")
    assert item.submitPassword(secret, secret)
    assert item.currentScreen == "backup"
    assert len(item.backupWords) == 12
    assert not (tmp_path / "wallet-vault.json").exists()
    assert item.finishBackup()
    assert item.currentScreen == "main"
    assert item.backupWords == []
    assert len(item.profiles) == 1
    assert item.profiles[0]["typeLabel"] == "Seed phrase"


def test_locked_restart_rejects_wrong_password_without_session(tmp_path) -> None:
    original = controller(tmp_path)
    secret = password()
    original.beginCreate()
    assert original.submitPassword(secret, secret)
    assert original.finishBackup()

    restarted = controller(tmp_path)
    assert restarted.currentScreen == "password"
    assert restarted.passwordTitle == "Unlock Wallet"
    assert not restarted.passwordConfirmRequired
    assert not restarted.submitPassword(password(), "")
    assert restarted.errorMessage == "Authentication failed"
    assert restarted.submitPassword(secret, "")
    assert restarted.currentScreen == "main"
    assert restarted.passwordTitle == "Enter Password"


def test_first_import_supports_seed_and_existing_vault_adds_only_raw_key(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    mnemonic = generate_mnemonic().value

    item.beginImport()
    assert item.currentScreen == "import"
    assert item.submitImport("seed", mnemonic)
    assert item.submitPassword(secret, secret)
    assert item.activeProfile["label"] == "Main Account"

    item.showWallets()
    item.beginAddPrivateKey()
    assert item.importPrivateOnly
    assert not item.submitImport("seed", mnemonic)
    assert item.submitImport("private", raw_private_key())
    assert item.submitPassword(secret, "")
    assert len(item.profiles) == 2
    assert item.activeProfile["label"] == "Account 2"
    assert item.activeProfile["typeLabel"] == "Private key"


def test_cancel_and_unknown_selection_leave_state_unchanged(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    item.cancelFlow()

    assert item.currentScreen == "welcome"
    assert item.backupWords == []
    assert list(tmp_path.iterdir()) == []
    assert not item.selectProfile("unknown")


def test_unsigned_preflight_writes_public_history_without_authentication(
    tmp_path, monkeypatch,
) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    vault_before = item._repository.paths.vault.read_bytes()

    def forbidden_authentication(_password: str):
        raise AssertionError("Unsigned preflight touched vault authentication")

    monkeypatch.setattr(item._repository, "authenticate", forbidden_authentication)
    item.showSend()
    assert item.currentScreen == "send"
    assert item.prepareTransfer("0x" + "44" * 20)
    assert item.currentScreen == "transfer_review"
    assert item.transferAction["network"] == "Base"
    assert item.transferAction["amount"] == "1 USDC"
    assert item.transferAction["maxTotalFeeWei"].isdigit()
    assert "data" not in item.transferAction
    assert item.historyRecords[0]["status"] == HistoryStatus.PREPARED.value
    assert item.historyRecords[0]["simulated"] is False
    assert item._repository.paths.vault.read_bytes() == vault_before

    item.finishTransfer()
    assert item.currentScreen == "main"
    assert item._transfer_flow.state is TransferFlowState.LOCKED


def test_generalized_draft_binds_network_asset_recipient_and_exact_amount(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    recipient = "0x" + "44" * 20

    item.showSend()
    assert item.transferNetwork == ""
    assert item.transferAsset == ""
    assert item.prepareTransfer("ethereum", "eth", recipient, "0,001")
    assert item.currentScreen == "transfer_review"
    assert item.transferAction["networkId"] == "ethereum"
    assert item.transferAction["assetId"] == "eth"
    assert item.transferAction["amount"] == "0.001 ETH"
    assert item.transferAction["recipient"].endswith("444444")

    item.editTransfer()
    assert item.transferNetwork == "ethereum"
    assert item.transferAsset == "eth"
    assert item.transferAmountInput == "0.001"
    assert item.transferRecipient.endswith("444444")
    assert item.transferAction == {}


def test_configured_amount_cap_refuses_before_rpc(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item._mainnet_executor.policy = MainnetBroadcastPolicy(
        True,
        OfflineSigningPolicy(10**18),
        amount_limits={("base", "usdc"): 999_999},
    )

    item.showSend()
    assert not item.prepareTransfer("base", "usdc", "0x" + "44" * 20, "1")
    assert item.transferError == "Amount exceeds the local route limit"
    assert item._transfer_preflight_service.calls == []


def test_maximum_amount_uses_token_cap_and_live_native_fee_quote(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    recipient = "0x" + "44" * 20
    ready = []
    item.transferMaximumReady.connect(lambda *values: ready.append(values))
    item._mainnet_executor.policy = MainnetBroadcastPolicy(
        True,
        OfflineSigningPolicy(10**18),
        amount_limits={
            ("base", "usdc"): 2_000_000,
            ("base", "eth"): 2**256 - 1,
        },
    )

    assert item.maximumTransferAmount("base", "usdc") == "2"
    assert item.maximumTransferAmount("base", "eth") == ""
    assert item.requestMaximumTransfer("base", "usdc", "")
    assert ready[-1] == ("base", "usdc", "", "2")

    assert item.requestMaximumTransfer("base", "eth", recipient)
    max_fee_per_gas = 2 * 10_000_000 + 1_000_000
    expected = 10**18 - 60_500 * max_fee_per_gas
    assert ready[-1] == (
        "base", "eth", recipient, format_atomic_amount(expected, 18),
    )
    assert not item.transferMaximumQuoting
    assert not item.requestMaximumTransfer("base", "eth", "invalid")
    assert item.transferError == "Enter a valid EVM recipient address"


def test_mainnet_execution_submits_once_and_updates_public_history(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    vault_before = item._repository.paths.vault.read_bytes()

    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action_id = item.transferAction["actionId"]
    assert item.mainnetExecutionAvailable
    assert item.mainnetFeeLimit.endswith("ETH")
    assert item.beginMainnetExecution()
    assert item.currentScreen == "sign_transfer"
    assert not item.submitMainnetExecution(secret, False)
    assert item.submitMainnetExecution(secret, True)

    assert item.currentScreen == "transfer_result"
    assert item.mainnetResult["code"] == "PENDING"
    assert item.mainnetResult["actionId"] == action_id
    assert item.mainnetResult["transactionHash"].startswith("0x")
    assert item.mainnetResult["recoveredSigner"] == item.activeProfile["address"]
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    assert item.historyRecords[0]["status"] == HistoryStatus.PENDING.value
    assert item.historyRecords[0]["transactionHash"].startswith("0x")
    assert item._test_mainnet_rpc.send_calls == 1
    assert item._repository.paths.vault.read_bytes() == vault_before

    item.finishMainnetExecution()
    assert item.currentScreen == "main"
    assert item.mainnetResult == {}


def test_manual_receipt_check_confirms_exact_public_transfer(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action = item._transfer_flow.current
    assert action is not None
    assert item.beginMainnetExecution()
    assert item.submitMainnetExecution(secret, True)
    transaction_hash = item.mainnetResult["transactionHash"]
    sender_topic = "0x" + action.sender[2:].lower().rjust(64, "0")
    recipient_topic = "0x" + action.recipient[2:].lower().rjust(64, "0")
    item._test_mainnet_rpc.receipt = {
        "transactionHash": transaction_hash,
        "from": action.sender,
        "to": action.token_contract,
        "status": 1,
        "gasUsed": 45_000,
        "effectiveGasPrice": 12,
        "logs": [
            {
                "address": action.token_contract,
                "topics": [TRANSFER_EVENT_TOPIC, sender_topic, recipient_topic],
                "data": "0x" + action.amount_atomic.to_bytes(32, "big").hex(),
            },
        ],
    }

    assert item.checkMainnetStatus(action.action_id)
    assert item.mainnetResult["code"] == "CONFIRMED"
    assert item.historyRecords[0]["status"] == "confirmed"


def test_wrong_password_cancel_and_late_execution_result_are_terminal(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    first_id = item.transferAction["actionId"]
    assert item.beginMainnetExecution()
    assert item.submitMainnetExecution(password(), True)
    assert item.currentScreen == "transfer_result"
    assert item.mainnetResult["code"] == "AUTHENTICATION_FAILED"
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    item.finishMainnetExecution()

    deferred = DeferredExecutor()
    repository = item._repository
    history = HistoryStore(repository.paths)
    mainnet, tracker, _rpc = mainnet_services(repository, history)
    second = WalletController(
        repository,
        StubPublicDataService(),
        history,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=deferred,
        mainnet_executor=mainnet,
        receipt_tracker=tracker,
        receipt_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    assert second.submitPassword(secret, "")
    second.showSend()
    assert second.prepareTransfer("0x" + "55" * 20)
    deferred.run_next()
    assert second.currentScreen == "transfer_review"
    assert second.transferAction["actionId"] != first_id
    assert second.beginMainnetExecution()
    assert second.submitMainnetExecution(secret, True)
    assert second.mainnetExecutionInProgress
    assert not second.canCloseWallet
    second.shutdown()
    deferred.run_next()
    assert second.mainnetResult == {}
    assert second._transfer_flow.state is TransferFlowState.LOCKED


def test_missing_or_exceeded_local_fee_limit_disables_password_flow(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    history = HistoryStore(repository.paths)
    mainnet, tracker, _rpc = mainnet_services(repository, history, enabled=False)
    item = WalletController(
        repository,
        StubPublicDataService(),
        history,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        mainnet_executor=mainnet,
        receipt_tracker=tracker,
        receipt_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    assert not item.mainnetExecutionAvailable
    assert item.mainnetFeeLimit == "Not configured"
    assert "HOLON_BASE_BROADCAST_ENABLED" in item.mainnetGateMessage
    assert not item.beginMainnetExecution()
    assert item.currentScreen == "transfer_review"


def test_mutation_expiry_profile_change_and_executor_failure_are_terminal(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action = item._transfer_flow.current
    assert action is not None
    item._transfer_flow._current = replace(
        action,
        transaction=replace(action.transaction, nonce=action.transaction.nonce + 1),
    )
    assert item.beginMainnetExecution()
    assert not item.submitMainnetExecution(secret, True)
    assert item.currentScreen == "transfer_result"
    assert item.mainnetResult["code"] == "ACTION_INVALID"
    item.finishMainnetExecution()

    item.showSend()
    assert item.prepareTransfer("0x" + "55" * 20)
    assert item.beginMainnetExecution()
    action = item._transfer_flow.current
    assert action is not None
    item._transfer_flow._current = replace(
        action, expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    item._expire_transfer()
    assert item.currentScreen == "transfer_result"
    assert item.mainnetResult["code"] == "ACTION_EXPIRED"
    item.finishMainnetExecution()

    second = item._repository.new_record(import_private_key(raw_private_key()), "Account 2")
    profiles = item._repository.append(secret, second)
    item._replace_profiles(profiles, profiles[0].profile_id)
    item.showSend()
    assert item.prepareTransfer("0x" + "66" * 20)
    assert item.beginMainnetExecution()
    assert item.selectProfile(second.summary.profile_id)
    assert item.currentScreen == "main"
    assert item._transfer_flow.state is TransferFlowState.LOCKED

    class FailingExecutor:
        policy = MainnetBroadcastPolicy(True, OfflineSigningPolicy(10**18))

        @staticmethod
        def execute(*_args):
            raise RuntimeError("secret-bearing-internal-canary")

    failing_repository = VaultRepository(WalletPaths(tmp_path / "failing"))
    failing_history = HistoryStore(failing_repository.paths)
    _mainnet, failing_tracker, _rpc = mainnet_services(
        failing_repository, failing_history,
    )
    failing = WalletController(
        failing_repository,
        StubPublicDataService(),
        failing_history,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        mainnet_executor=FailingExecutor(),
        receipt_tracker=failing_tracker,
        receipt_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    failure_password = password()
    failing.beginCreate()
    assert failing.submitPassword(failure_password, failure_password)
    assert failing.finishBackup()
    failing.showSend()
    assert failing.prepareTransfer("0x" + "77" * 20)
    assert failing.beginMainnetExecution()
    assert failing.submitMainnetExecution(failure_password, True)
    assert failing.currentScreen == "transfer_result"
    assert failing.mainnetResult["code"] == "SIGNING_FAILED"
    assert "canary" not in repr(failing.mainnetResult).lower()


def test_transfer_invalid_edit_and_profile_change_are_terminal(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    item.showSend()
    assert not item.prepareTransfer("not-an-address")
    assert item.transferError == "Enter a valid EVM recipient address"
    assert item.historyRecords == []

    assert item.prepareTransfer("0x" + "44" * 20)
    first_id = item.transferAction["actionId"]
    item.editTransfer()
    assert item.currentScreen == "send"
    assert item.transferRecipient == "0x" + "44" * 20
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    assert item.prepareTransfer(item.transferRecipient)
    assert item.transferAction["actionId"] != first_id

    second = item._repository.new_record(import_private_key(raw_private_key()), "Account 2")
    profiles = item._repository.append(secret, second)
    item._replace_profiles(profiles, profiles[0].profile_id)
    assert item.selectProfile(second.summary.profile_id)
    assert item.currentScreen == "main"
    assert item._transfer_flow.state is TransferFlowState.LOCKED


def test_transfer_failure_is_safe_and_writes_no_history(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    service = StubTransferPreflightService(
        TransferPreflightError(TransferPreflightCode.INSUFFICIENT_USDC),
    )
    item = WalletController(
        repository,
        StubPublicDataService(),
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=service,
        transfer_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()

    assert item.prepareTransfer("0x" + "44" * 20)
    assert item.currentScreen == "send"
    assert item.transferError == "Insufficient USDC for this transfer"
    assert item.historyRecords == []
    assert not repository.paths.history.exists()


def test_cancelled_preflight_ignores_late_response(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    executor = DeferredExecutor()
    item = WalletController(
        repository,
        StubPublicDataService(),
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=executor,
        price_service=StubPriceService(),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    assert item.transferPreparing

    item.cancelTransfer()
    assert item.currentScreen == "main"
    executor.run_next()

    assert item.currentScreen == "main"
    assert item.transferAction == {}
    assert item.historyRecords == []
    assert not repository.paths.history.exists()


def test_history_failure_blocks_review_and_preserves_previous_file(
    tmp_path, monkeypatch,
) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    item.finishTransfer()
    before = item._repository.paths.history.read_bytes()

    def failed_append(_record):
        raise StorageError("write-canary")

    monkeypatch.setattr(item._history_store, "append", failed_append)
    item.showSend()
    assert item.prepareTransfer("0x" + "55" * 20)

    assert item.currentScreen == "send"
    assert item.transferError == "History unavailable · transaction was not prepared"
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    assert item._repository.paths.history.read_bytes() == before


def test_prepared_transfer_expiry_returns_to_form(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action = item._transfer_flow.current
    assert action is not None
    item._transfer_flow._current = replace(
        action,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    item._expire_transfer()

    assert item.currentScreen == "send"
    assert item.transferError == "Transaction preparation expired"
    assert item._transfer_flow.state is TransferFlowState.LOCKED


def test_public_refresh_filter_and_stale_result_are_safe(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    service = StubPublicDataService()
    item = WalletController(
        repository,
        service,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    assert service.calls[-1][2] == ("ethereum", "base")
    assert item.publicDataBanner == "LOCAL WALLET  ·  LIVE PUBLIC DATA"
    assert item.ethereumData["ethValue"] == "1 ETH"
    assert item.baseData["usdcValue"] == "2.5 USDC"
    assert item.selectNetwork("base")
    assert service.calls[-1][2] == ("base",)
    assert item.selectedNetwork == "base"
    assert not item.selectNetwork("arbitrum")

    current = dict(item.baseData)
    stale = public_snapshot("base", eth=99 * 10**18)
    item._accept_public_data(
        item._public_data_generation - 1,
        type("Snapshot", (), {
            "profile_id": item.activeProfileId,
            "address": item.activeProfile["address"],
            "networks": (stale,),
        })(),
    )
    assert item.baseData == current


def test_corrupt_history_degrades_only_history_screen(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item._history_store.path.write_text("not-json", encoding="utf-8")

    item.showHistory()

    assert item.currentScreen == "history"
    assert not item.historyAvailable
    assert item.historyStateLabel == "History unavailable"
    assert item.profiles


def test_public_refresh_never_authenticates_or_decrypts_vault(tmp_path, monkeypatch) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    service = StubPublicDataService()
    item = WalletController(
        repository,
        service,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    def forbidden_authentication(_password: str):
        raise AssertionError("Public refresh touched vault authentication")

    monkeypatch.setattr(repository, "authenticate", forbidden_authentication)
    assert item.refreshPublicData()
    assert service.calls[-1][0] == item.activeProfileId

    second = item._repository.new_record(import_private_key(raw_private_key()), "Account 2")
    profiles = item._repository.append(secret, second)
    item._replace_profiles(profiles, profiles[0].profile_id)
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    assert item.currentScreen == "transfer_review"
    item.editTransfer()
    assert item.selectProfile(second.summary.profile_id)
    assert item.currentScreen == "send"
    assert item._transfer_flow.state is TransferFlowState.LOCKED


def test_v2_routes_visibility_and_public_details_are_memory_only(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    assert item.portfolioData["totalAvailable"] is True
    assert item.portfolioData["totalUsd"] == "$5,005.00"
    assert item.balancesVisible
    item.toggleBalancesVisibility()
    assert not item.balancesVisible

    item.showReceive()
    assert item.currentScreen == "receive"
    assert item.receiveQrSource.endswith(item.activeProfile["address"])
    assert item.selectReceiveNetwork("ethereum")
    assert item.receiveNetwork == "ethereum"
    assert not item.selectReceiveNetwork("arbitrum")

    item.showSettings()
    assert item.currentScreen == "settings"
    assert item.showSettingsSection("security")
    assert item.currentScreen == "settings_info"
    assert item.settingsSection == "security"
    item.closeSettingsInfo()
    item.showWallets()
    assert item.currentScreen == "wallets"
    item.closeWallets()
    assert item.currentScreen == "settings"

    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action_id = item.transferAction["actionId"]
    item.cancelTransfer()
    item.showHistory()
    assert item.showTransactionDetails(action_id)
    assert item.currentScreen == "transaction_details"
    assert item.selectedHistoryRecord["actionId"] == action_id
    assert item.selectedHistoryRecord["maxTotalFeeWei"]
    item.closeTransactionDetails()
    assert item.currentScreen == "history"
