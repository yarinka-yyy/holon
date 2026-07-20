from __future__ import annotations

import secrets

from holon_wallet.controller import WalletController
from holon_wallet.authority import WalletAuthorityState
from holon_wallet.storage import WalletPaths
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic, import_private_key


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
    return WalletController(VaultRepository(WalletPaths(tmp_path)))


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

    second = item._repository.new_record(import_private_key(raw_private_key()), "Account 2")
    profiles = item._repository.append(secret, second)
    item._replace_profiles(profiles, profiles[0].profile_id)
    assert item.beginMockAction()
    assert item.selectProfile(second.summary.profile_id)
    assert item.currentScreen == "main"
    assert item._authority.state is WalletAuthorityState.LOCKED
