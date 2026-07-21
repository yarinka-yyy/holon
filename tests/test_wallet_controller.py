from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from holon_wallet.controller import WalletController
from holon_wallet.history import HistoryStatus
from holon_wallet.signer import OfflineSigningPolicy, OfflineTransferSigner
from holon_wallet.storage import StorageError, WalletPaths
from holon_wallet.transfer import TransferFlowState, TransferPreflightCode, TransferPreflightError
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic, import_private_key
from wallet_public_support import (
    DeferredExecutor,
    ImmediateExecutor,
    StubPublicDataService,
    StubTransferPreflightService,
    public_snapshot,
)


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
    return WalletController(
        repository,
        StubPublicDataService(),
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        offline_signer=OfflineTransferSigner(
            repository, OfflineSigningPolicy(10**18),
        ),
    )


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


def test_offline_signing_success_returns_public_proof_and_keeps_history_prepared(
    tmp_path,
) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    vault_before = item._repository.paths.vault.read_bytes()

    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action_id = item.transferAction["actionId"]
    assert item.offlineSigningAvailable
    assert item.offlineSigningLimit.endswith("ETH")
    assert item.beginOfflineSigning()
    assert item.currentScreen == "sign_transfer"
    assert item.submitOfflineSigning(secret)

    assert item.currentScreen == "sign_result"
    assert item.offlineSigningResult["success"] is True
    assert item.offlineSigningResult["actionId"] == action_id
    assert item.offlineSigningResult["transactionHash"].startswith("0x")
    assert item.offlineSigningResult["recoveredSigner"] == item.activeProfile["address"]
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    assert item.historyRecords[0]["status"] == HistoryStatus.PREPARED.value
    assert item.historyRecords[0]["transactionHash"] == ""
    assert item._repository.paths.vault.read_bytes() == vault_before

    item.finishOfflineSigning()
    assert item.currentScreen == "main"
    assert item.offlineSigningResult == {}


def test_wrong_password_cancel_and_late_signing_result_are_terminal(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    first_id = item.transferAction["actionId"]
    assert item.beginOfflineSigning()
    assert item.submitOfflineSigning(password())
    assert item.currentScreen == "sign_result"
    assert item.offlineSigningResult["code"] == "AUTHENTICATION_FAILED"
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    item.finishOfflineSigning()

    deferred = DeferredExecutor()
    repository = item._repository
    second = WalletController(
        repository,
        StubPublicDataService(),
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=deferred,
        offline_signer=OfflineTransferSigner(
            repository, OfflineSigningPolicy(10**18),
        ),
    )
    assert second.submitPassword(secret, "")
    second.showSend()
    assert second.prepareTransfer("0x" + "55" * 20)
    deferred.run_next()
    assert second.currentScreen == "transfer_review"
    assert second.transferAction["actionId"] != first_id
    assert second.beginOfflineSigning()
    assert second.submitOfflineSigning(secret)
    assert second.offlineSigningInProgress
    second.cancelOfflineSigning()
    deferred.run_next()
    assert second.currentScreen == "main"
    assert second.offlineSigningResult == {}
    assert second._transfer_flow.state is TransferFlowState.LOCKED


def test_missing_or_exceeded_local_fee_limit_disables_password_flow(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    item = WalletController(
        repository,
        StubPublicDataService(),
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        offline_signer=OfflineTransferSigner(
            repository, OfflineSigningPolicy(None),
        ),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    assert not item.offlineSigningAvailable
    assert item.offlineSigningLimit == "Not configured"
    assert "HOLON_BASE_MAX_TOTAL_FEE_WEI" in item.offlineSigningGateMessage
    assert not item.beginOfflineSigning()
    assert item.currentScreen == "transfer_review"


def test_mutation_expiry_profile_change_and_signer_failure_are_terminal(tmp_path) -> None:
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
    assert item.beginOfflineSigning()
    assert not item.submitOfflineSigning(secret)
    assert item.currentScreen == "sign_result"
    assert item.offlineSigningResult["code"] == "ACTION_INVALID"
    item.finishOfflineSigning()

    item.showSend()
    assert item.prepareTransfer("0x" + "55" * 20)
    assert item.beginOfflineSigning()
    action = item._transfer_flow.current
    assert action is not None
    item._transfer_flow._current = replace(
        action, expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    item._expire_transfer()
    assert item.currentScreen == "sign_result"
    assert item.offlineSigningResult["code"] == "ACTION_EXPIRED"
    item.finishOfflineSigning()

    second = item._repository.new_record(import_private_key(raw_private_key()), "Account 2")
    profiles = item._repository.append(secret, second)
    item._replace_profiles(profiles, profiles[0].profile_id)
    item.showSend()
    assert item.prepareTransfer("0x" + "66" * 20)
    assert item.beginOfflineSigning()
    assert item.selectProfile(second.summary.profile_id)
    assert item.currentScreen == "main"
    assert item._transfer_flow.state is TransferFlowState.LOCKED

    class FailingSigner:
        policy = OfflineSigningPolicy(10**18)

        @staticmethod
        def sign(*_args):
            raise RuntimeError("secret-bearing-internal-canary")

    failing_repository = VaultRepository(WalletPaths(tmp_path / "failing"))
    failing = WalletController(
        failing_repository,
        StubPublicDataService(),
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        offline_signer=FailingSigner(),
    )
    failure_password = password()
    failing.beginCreate()
    assert failing.submitPassword(failure_password, failure_password)
    assert failing.finishBackup()
    failing.showSend()
    assert failing.prepareTransfer("0x" + "77" * 20)
    assert failing.beginOfflineSigning()
    assert failing.submitOfflineSigning(failure_password)
    assert failing.currentScreen == "sign_result"
    assert failing.offlineSigningResult["code"] == "SIGNING_FAILED"
    assert "canary" not in repr(failing.offlineSigningResult).lower()


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
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()

    assert item.prepareTransfer("0x" + "44" * 20)
    assert item.currentScreen == "send"
    assert item.transferError == "This Account does not have 1 USDC on Base"
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
