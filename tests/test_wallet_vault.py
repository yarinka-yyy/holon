from __future__ import annotations

import base64
import json
import secrets
from dataclasses import replace

import pytest
from bip_utils import Bip39Languages, Bip39MnemonicGenerator, Bip39WordsNum

import holon_wallet.vault as vault_module
from holon_wallet.settings import SettingsStore
from holon_wallet.storage import StorageError, WalletPaths, atomic_write_json
from holon_wallet.vault import (
    AuthenticationFailedError,
    ProfileRecord,
    VaultRepository,
    VaultUnavailableError,
    VaultValidationError,
)
from holon_wallet.wallet_crypto import (
    DERIVATION_PATH,
    MNEMONIC_PROFILE,
    RAW_KEY_PROFILE,
    generate_mnemonic,
    import_mnemonic,
    import_private_key,
)


def new_password() -> str:
    return secrets.token_urlsafe(18)


def new_private_material():
    while True:
        try:
            return import_private_key(secrets.token_hex(32))
        except ValueError:
            continue


def repository(tmp_path) -> VaultRepository:
    return VaultRepository(WalletPaths(tmp_path))


def test_mnemonic_generation_and_import_formats() -> None:
    generated = generate_mnemonic()
    imported = import_mnemonic(generated.value)
    words_24 = str(
        Bip39MnemonicGenerator(Bip39Languages.ENGLISH).FromWordsNumber(
            Bip39WordsNum.WORDS_NUM_24,
        ),
    )
    imported_24 = import_mnemonic(words_24)

    assert len(generated.value.split()) == 12
    assert imported.address == generated.address
    assert imported.profile_type == MNEMONIC_PROFILE
    assert imported.derivation_path == DERIVATION_PATH
    assert len(imported_24.value.split()) == 24
    assert len(imported_24.address) == 42


def test_create_authenticate_and_append_multiple_profile_types(tmp_path) -> None:
    repo = repository(tmp_path)
    password = new_password()
    mnemonic = generate_mnemonic()
    first = repo.new_record(mnemonic, "Main Account")
    profiles = repo.create_new(password, first)
    vault_text = repo.paths.vault.read_text(encoding="utf-8")

    assert profiles == (first.summary,)
    assert mnemonic.value not in vault_text
    assert first.summary.address in vault_text
    assert repo.authenticate(password) == profiles

    raw = new_private_material()
    second = repo.new_record(raw, "Account 2")
    updated = repo.append(password, second)
    assert [item.profile_type for item in updated] == [MNEMONIC_PROFILE, RAW_KEY_PROFILE]
    assert repo.authenticate(password) == updated
    assert raw.value not in repo.paths.vault.read_text(encoding="utf-8")


def test_each_save_uses_fresh_salt_nonce_and_ciphertext(tmp_path) -> None:
    repo = repository(tmp_path)
    password = new_password()
    first = repo.new_record(generate_mnemonic(), "Main Account")
    repo.create_new(password, first)
    before = json.loads(repo.paths.vault.read_text(encoding="utf-8"))
    repo.append(password, repo.new_record(new_private_material(), "Account 2"))
    after = json.loads(repo.paths.vault.read_text(encoding="utf-8"))

    assert before["kdf"]["salt"] != after["kdf"]["salt"]
    assert before["cipher"]["nonce"] != after["cipher"]["nonce"]
    assert before["ciphertext"] != after["ciphertext"]


def test_wrong_password_and_authenticated_mutations_share_generic_failure(tmp_path) -> None:
    repo = repository(tmp_path)
    password = new_password()
    repo.create_new(password, repo.new_record(generate_mnemonic(), "Main Account"))

    with pytest.raises(AuthenticationFailedError, match="^Authentication failed$"):
        repo.authenticate(new_password())

    document = json.loads(repo.paths.vault.read_text(encoding="utf-8"))
    document["public"]["profiles"][0]["label"] = "Changed"
    atomic_write_json(repo.paths.vault, document)
    with pytest.raises(AuthenticationFailedError, match="^Authentication failed$"):
        repo.authenticate(password)


def test_ciphertext_tamper_and_valid_ciphertext_address_mismatch_fail_closed(tmp_path) -> None:
    repo = repository(tmp_path)
    password = new_password()
    record = repo.new_record(generate_mnemonic(), "Main Account")
    repo.create_new(password, record)
    document = json.loads(repo.paths.vault.read_text(encoding="utf-8"))
    encrypted = bytearray(base64.b64decode(document["ciphertext"]))
    encrypted[-1] ^= 1
    document["ciphertext"] = base64.b64encode(encrypted).decode("ascii")
    atomic_write_json(repo.paths.vault, document)
    with pytest.raises(AuthenticationFailedError, match="^Authentication failed$"):
        repo.authenticate(password)

    wrong_summary = replace(record.summary, address=new_private_material().address)
    mismatched = ProfileRecord(wrong_summary, record.secret)
    atomic_write_json(repo.paths.vault, repo._encrypt(password, (mismatched,)))
    with pytest.raises(AuthenticationFailedError, match="^Authentication failed$"):
        repo.authenticate(password)


def test_unsupported_or_malformed_schema_never_becomes_first_run(tmp_path) -> None:
    repo = repository(tmp_path)
    atomic_write_json(repo.paths.vault, {"schema_version": 999})

    assert repo.exists
    with pytest.raises(VaultUnavailableError):
        repo.load_public()
    with pytest.raises(VaultValidationError, match="Wallet already exists"):
        repo.prepare_new(new_password(), repo.new_record(generate_mnemonic(), "Main Account"))


def test_duplicate_and_failed_replace_preserve_original_vault(tmp_path, monkeypatch) -> None:
    repo = repository(tmp_path)
    password = new_password()
    first = repo.new_record(generate_mnemonic(), "Main Account")
    repo.create_new(password, first)
    with pytest.raises(VaultValidationError, match="already exists"):
        repo.append(password, repo.new_record(first.secret, "Account 2"))
    before = repo.paths.vault.read_bytes()

    def fail_write(*_args, **_kwargs):
        raise StorageError("safe failure")

    monkeypatch.setattr(vault_module, "atomic_write_json", fail_write)
    with pytest.raises(StorageError):
        repo.append(password, repo.new_record(new_private_material(), "Account 2"))
    assert repo.paths.vault.read_bytes() == before


def test_settings_are_public_atomic_and_invalid_values_fall_back(tmp_path) -> None:
    paths = WalletPaths(tmp_path)
    store = SettingsStore(paths)
    valid = "00000000-0000-4000-8000-000000000001"
    store.save_active_id(valid)

    assert store.load_active_id({valid}) == valid
    assert store.load_active_id({"different"}) is None
    paths.settings.write_text("not-json", encoding="utf-8")
    assert store.load_active_id({valid}) is None


def test_password_policy_and_errors_never_include_secret_canaries(tmp_path, capsys) -> None:
    repo = repository(tmp_path)
    password = new_password()
    secret = generate_mnemonic()
    with pytest.raises(VaultValidationError, match="at least 4"):
        repo.prepare_new("abc", repo.new_record(secret, "Main Account"))
    repo.create_new(password, repo.new_record(secret, "Main Account"))
    wrong = new_password()
    with pytest.raises(AuthenticationFailedError) as captured:
        repo.authenticate(wrong)

    output = capsys.readouterr()
    combined = str(captured.value) + output.out + output.err
    assert password not in combined
    assert wrong not in combined
    assert secret.value not in combined
