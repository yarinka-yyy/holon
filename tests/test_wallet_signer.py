from __future__ import annotations

import re
import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from web3 import Web3

from holon_wallet.signer import (
    FEE_LIMIT_ENV,
    OfflineSigningCode,
    OfflineSigningPolicy,
    OfflineTransferSigner,
    offline_signing_result_to_map,
)
from holon_wallet.storage import WalletPaths
from holon_wallet.transfer import (
    PendingTransferRequest,
    SigningPermit,
    TransferFlowCoordinator,
    TransferFlowError,
    TransferFlowState,
    TransferPreflightService,
    transfer_route,
)
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic, import_private_key

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
RECIPIENT = Web3.to_checksum_address("0x" + "44" * 20)
HASH_RE = re.compile(r"^0x[0-9a-f]{64}$")


class SigningRpc:
    def __init__(self, chain_id=8453):
        self._chain_id = chain_id

    def chain_id(self):
        return self._chain_id

    def latest_block(self):
        return 123_456, 10

    def native_balance(self, _address):
        return 10**18

    def token_decimals(self, _contract):
        return 6

    def token_balance(self, _contract, _address):
        return 2_000_000

    def pending_nonce(self, _address):
        return 7

    def max_priority_fee_per_gas(self):
        return 2

    def estimate_gas(self, _transaction):
        return 50_000


def new_password() -> str:
    return secrets.token_urlsafe(18)


def new_raw_key():
    while True:
        try:
            return import_private_key(secrets.token_hex(32))
        except ValueError:
            continue


def repository_and_action(tmp_path, profile_type: str = "mnemonic"):
    password = new_password()
    repository = VaultRepository(WalletPaths(tmp_path))
    material = generate_mnemonic() if profile_type == "mnemonic" else new_raw_key()
    record = repository.new_record(material, "Main Account")
    repository.create_new(password, record)
    request = PendingTransferRequest(
        "act-sign",
        record.summary.profile_id,
        NOW,
        NOW + timedelta(minutes=5),
    )
    service = TransferPreflightService(lambda _endpoint: SigningRpc(), environ={})
    action = service.prepare(request, record.summary, RECIPIENT)
    return repository, action, password, material.value


@pytest.mark.parametrize("profile_type", ["mnemonic", "raw_private_key"])
def test_signs_and_verifies_exact_type2_for_both_profile_types(
    tmp_path, profile_type,
) -> None:
    repository, action, password, secret_canary = repository_and_action(
        tmp_path, profile_type,
    )
    vault_before = repository.paths.vault.read_bytes()
    signer = OfflineTransferSigner(
        repository, OfflineSigningPolicy(10**18), lambda: NOW,
    )

    result = signer.sign(action, action.digest, password, SigningPermit())
    mapped = offline_signing_result_to_map(result)

    assert result.success and result.code is OfflineSigningCode.SUCCESS
    assert result.recovered_signer == action.sender
    assert HASH_RE.fullmatch(result.transaction_hash)
    assert mapped["transactionHash"] == result.transaction_hash
    assert mapped["shortRecoveredSigner"]
    assert "rawTransaction" not in mapped and "signedBytes" not in mapped
    assert secret_canary not in repr(result)
    assert password not in repr(result)
    assert repository.paths.vault.read_bytes() == vault_before
    assert not repository.paths.history.exists()


@pytest.mark.parametrize(
    ("network_id", "asset_id", "amount_atomic"),
    [
        ("ethereum", "eth", 10**15),
        ("ethereum", "usdc", 1_500_000),
        ("base", "eth", 10**15),
        ("base", "usdc", 1_500_000),
    ],
)
def test_signer_accepts_all_exact_allowlisted_routes(
    tmp_path, network_id, asset_id, amount_atomic,
) -> None:
    password = new_password()
    repository = VaultRepository(WalletPaths(tmp_path))
    record = repository.new_record(generate_mnemonic(), "Main Account")
    repository.create_new(password, record)
    route = transfer_route(network_id, asset_id)
    request = PendingTransferRequest(
        "act-route", record.summary.profile_id, NOW, NOW + timedelta(minutes=5),
        network_id, asset_id, amount_atomic,
    )
    rpc = SigningRpc(route.chain_id)
    action = TransferPreflightService(
        lambda _endpoint: rpc, environ={route.endpoint_env: "fixture://route"},
    ).prepare(request, record.summary, RECIPIENT)

    result = OfflineTransferSigner(
        repository, OfflineSigningPolicy(10**18), lambda: NOW,
    ).sign(action, action.digest, password, SigningPermit())
    assert result.success
    assert result.recovered_signer == action.sender


def test_fee_policy_is_explicit_strict_and_fail_closed(tmp_path) -> None:
    _repository, action, _password, _secret = repository_and_action(tmp_path)
    assert not OfflineSigningPolicy.from_environment({}).available
    assert not OfflineSigningPolicy.from_environment({FEE_LIMIT_ENV: "0"}).available
    assert not OfflineSigningPolicy.from_environment({FEE_LIMIT_ENV: "not-a-number"}).available
    exact = OfflineSigningPolicy.from_environment(
        {FEE_LIMIT_ENV: str(action.max_total_fee_wei)},
    )
    assert exact.available and exact.evaluate(action) is None
    assert OfflineSigningPolicy(action.max_total_fee_wei - 1).evaluate(
        action,
    ) is OfflineSigningCode.FEE_LIMIT_EXCEEDED


@pytest.mark.parametrize(
    ("policy", "password_kind", "expected"),
    [
        (OfflineSigningPolicy(None), "correct", OfflineSigningCode.POLICY_UNAVAILABLE),
        (OfflineSigningPolicy(1), "correct", OfflineSigningCode.FEE_LIMIT_EXCEEDED),
        (OfflineSigningPolicy(10**18), "wrong", OfflineSigningCode.AUTHENTICATION_FAILED),
    ],
)
def test_policy_and_authentication_failures_return_only_safe_codes(
    tmp_path, policy, password_kind, expected,
) -> None:
    repository, action, password, secret_canary = repository_and_action(tmp_path)
    supplied = password if password_kind == "correct" else new_password()
    result = OfflineTransferSigner(repository, policy, lambda: NOW).sign(
        action, action.digest, supplied, SigningPermit(),
    )
    assert not result.success and result.code is expected
    assert not result.transaction_hash and not result.recovered_signer
    assert supplied not in repr(result)
    assert secret_canary not in repr(result)


def test_mutation_unknown_profile_expiry_and_cancel_fail_closed(tmp_path) -> None:
    repository, action, password, _secret = repository_and_action(tmp_path)
    signer = OfflineTransferSigner(
        repository, OfflineSigningPolicy(10**18), lambda: NOW,
    )
    mutated = replace(
        action,
        transaction=replace(action.transaction, nonce=action.transaction.nonce + 1),
    )
    assert signer.sign(
        mutated, action.digest, password, SigningPermit(),
    ).code is OfflineSigningCode.ACTION_INVALID

    unknown = replace(action, profile_id="unknown-profile")
    assert signer.sign(
        unknown, unknown.digest, password, SigningPermit(),
    ).code is OfflineSigningCode.AUTHENTICATION_FAILED

    expired_signer = OfflineTransferSigner(
        repository,
        OfflineSigningPolicy(10**18),
        lambda: action.expires_at,
    )
    assert expired_signer.sign(
        action, action.digest, password, SigningPermit(),
    ).code is OfflineSigningCode.ACTION_EXPIRED

    permit = SigningPermit()
    permit.cancel()
    assert signer.sign(
        action, action.digest, password, permit,
    ).code is OfflineSigningCode.CANCELLED


def test_expiry_after_authentication_prevents_key_use(tmp_path, monkeypatch) -> None:
    repository, action, password, _secret = repository_and_action(tmp_path)
    clock = [NOW]
    original = repository._authenticate_profile

    def authenticate_then_expire(password_value, profile_id):
        record = original(password_value, profile_id)
        clock[0] = action.expires_at
        return record

    monkeypatch.setattr(repository, "_authenticate_profile", authenticate_then_expire)
    signer = OfflineTransferSigner(
        repository, OfflineSigningPolicy(10**18), lambda: clock[0],
    )
    result = signer.sign(action, action.digest, password, SigningPermit())
    assert result.code is OfflineSigningCode.ACTION_EXPIRED


def test_flow_signing_is_single_use_terminal_and_replay_safe(tmp_path) -> None:
    repository, prepared, password, _secret = repository_and_action(tmp_path)
    ids = iter(("act-flow", "act-flow", "act-new"))
    flow = TransferFlowCoordinator(lambda: NOW, lambda: next(ids))
    pending = flow.begin(prepared.profile_id)
    action = replace(
        prepared,
        action_id=pending.action_id,
        created_at=pending.created_at,
        expires_at=pending.expires_at,
    )
    assert flow.accept(action)
    permit = flow.begin_signing(action.action_id, action.digest, action.profile_id)
    assert permit is not None and flow.state is TransferFlowState.SIGNING
    result = OfflineTransferSigner(
        repository, OfflineSigningPolicy(10**18), lambda: NOW,
    ).sign(action, flow.accepted_digest, password, permit)
    assert result.success
    assert flow.complete_signing(action.action_id)
    assert flow.state is TransferFlowState.LOCKED and permit.cancelled

    with pytest.raises(TransferFlowError):
        flow.begin(action.profile_id)
    assert flow.begin(action.profile_id).action_id == "act-new"


def test_second_signing_attempt_invalidates_active_permit(tmp_path) -> None:
    _repository, prepared, _password, _secret = repository_and_action(tmp_path)
    flow = TransferFlowCoordinator(lambda: NOW, lambda: "act-concurrent")
    pending = flow.begin(prepared.profile_id)
    action = replace(
        prepared,
        action_id=pending.action_id,
        created_at=pending.created_at,
        expires_at=pending.expires_at,
    )
    assert flow.accept(action)
    permit = flow.begin_signing(action.action_id, action.digest, action.profile_id)
    assert permit is not None
    assert flow.begin_signing(action.action_id, action.digest, action.profile_id) is None
    assert permit.cancelled and flow.state is TransferFlowState.LOCKED
