from __future__ import annotations

import secrets

from holon_wallet.controller import WalletController
from holon_wallet.authority import WalletAuthorityState
from holon_wallet.storage import WalletPaths
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic, import_private_key
from wallet_public_support import ImmediateExecutor, StubPublicDataService, public_snapshot


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
    return WalletController(
        VaultRepository(WalletPaths(tmp_path)),
        StubPublicDataService(),
        public_data_executor=ImmediateExecutor(),
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


def test_mock_action_success_is_single_use_and_writes_nothing(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    before = {
        path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
    }

    assert item.beginMockAction()
    action_id = item.mockAction["actionId"]
    assert item.currentScreen == "mock_review"
    assert item.mockAction["network"] == "Base"
    assert item.mockAction["amount"] == "1 USDC"
    assert not item.beginMockAction()
    assert item.continueMockAction()
    assert item.currentScreen == "mock_password"
    assert item.submitMockPassword(secret)

    assert item.currentScreen == "mock_result"
    assert item.actionResultSuccess
    assert "No transaction was signed or sent" in item.actionResultMessage
    assert item.mockAction == {}
    assert item._authority.state is WalletAuthorityState.LOCKED
    assert item._authority.preflight(action_id, "unused") is not None
    assert {
        path.name: path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
    } == before
    item.finishMockResult()
    assert item.currentScreen == "main"


def test_mock_action_wrong_password_cancel_and_profile_change_are_terminal(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    assert item.beginMockAction()
    assert item.continueMockAction()
    assert not item.submitMockPassword(password())
    assert item.currentScreen == "mock_result"
    assert item.actionResultTitle == "Authentication failed"
    assert item._authority.state is WalletAuthorityState.LOCKED

    item.finishMockResult()
    assert item.beginMockAction()
    item.cancelMockAction()
    assert item.currentScreen == "main"
    assert item._authority.state is WalletAuthorityState.LOCKED


def test_public_refresh_filter_and_stale_result_are_safe(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    service = StubPublicDataService()
    item = WalletController(
        repository, service, public_data_executor=ImmediateExecutor(),
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
        repository, service, public_data_executor=ImmediateExecutor(),
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
    assert item.beginMockAction()
    assert item.selectProfile(second.summary.profile_id)
    assert item.currentScreen == "main"
    assert item._authority.state is WalletAuthorityState.LOCKED
