from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from web3 import Web3

from holon_wallet.broadcast import (
    BASE_RPC_ENV,
    BROADCAST_ENABLED_ENV,
    TRANSFER_EVENT_TOPIC,
    BroadcastReceiptTracker,
    MainnetBroadcastPolicy,
    MainnetTransferCode,
    MainnetTransferExecutor,
    mainnet_result_to_map,
)
from holon_wallet.history import (
    HistoryStatus,
    HistoryStore,
    WalletHistoryRecord,
)
from holon_wallet.signer import FEE_LIMIT_ENV, OfflineSigningPolicy
from holon_wallet.storage import StorageError, WalletPaths
from holon_wallet.transfer import (
    PendingTransferRequest,
    SigningPermit,
    TransferPreflightService,
)
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic, import_private_key

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
RECIPIENT = Web3.to_checksum_address("0x" + "44" * 20)


class MainnetRpcStub:
    def __init__(self, **overrides) -> None:
        self.values = {
            "chain_id": 8453,
            "block": (123_456, 10),
            "native_balance": 10**18,
            "decimals": 6,
            "token_balance": 2_000_000,
            "nonce": 7,
            "priority_fee": 2,
            "gas": 50_000,
            "send_error": None,
            "remote_hash": None,
            "transaction": None,
            "receipt": None,
        }
        self.values.update(overrides)
        self.chain_calls = 0
        self.send_calls = 0
        self.receipt_calls = 0
        self.transaction_calls = 0

    def chain_id(self):
        self.chain_calls += 1
        return self.values["chain_id"]

    def latest_block(self):
        return self.values["block"]

    def native_balance(self, _address):
        return self.values["native_balance"]

    def token_decimals(self, _contract):
        return self.values["decimals"]

    def token_balance(self, _contract, _address):
        return self.values["token_balance"]

    def pending_nonce(self, _address):
        return self.values["nonce"]

    def max_priority_fee_per_gas(self):
        return self.values["priority_fee"]

    def estimate_gas(self, _transaction):
        return self.values["gas"]

    def send_raw_transaction(self, raw_transaction):
        self.send_calls += 1
        if self.values["send_error"] is not None:
            raise self.values["send_error"]
        return self.values["remote_hash"] or Web3.to_hex(Web3.keccak(raw_transaction))

    def transaction(self, _transaction_hash):
        self.transaction_calls += 1
        return self.values["transaction"]

    def transaction_receipt(self, _transaction_hash):
        self.receipt_calls += 1
        return self.values["receipt"]


def new_password() -> str:
    return secrets.token_urlsafe(18)


def new_raw_key():
    while True:
        try:
            return import_private_key(secrets.token_hex(32))
        except ValueError:
            continue


def prepared_fixture(tmp_path, profile_type: str = "mnemonic"):
    password = new_password()
    repository = VaultRepository(WalletPaths(tmp_path))
    material = generate_mnemonic() if profile_type == "mnemonic" else new_raw_key()
    record = repository.new_record(material, "Main Account")
    repository.create_new(password, record)
    rpc = MainnetRpcStub()
    request = PendingTransferRequest(
        "act-mainnet",
        record.summary.profile_id,
        NOW,
        NOW + timedelta(minutes=5),
    )
    action = TransferPreflightService(
        lambda _endpoint: rpc, environ={BASE_RPC_ENV: "fixture://base"},
    ).prepare(request, record.summary, RECIPIENT)
    history = HistoryStore(repository.paths)
    history.append(history_record(action))
    return repository, history, action, password, material.value, rpc


def history_record(action) -> WalletHistoryRecord:
    timestamp = "2026-07-21T12:00:00Z"
    return WalletHistoryRecord(
        action.action_id,
        action.profile_id,
        "transfer",
        action.network_id,
        action.chain_id,
        action.sender,
        action.recipient,
        action.token_contract,
        action.token,
        str(action.amount_atomic),
        action.decimals,
        None,
        HistoryStatus.PREPARED,
        timestamp,
        timestamp,
        False,
    )


def executor(repository, history, rpc, **changes):
    return MainnetTransferExecutor(
        repository,
        history,
        changes.pop(
            "policy",
            MainnetBroadcastPolicy(True, OfflineSigningPolicy(10**18)),
        ),
        lambda _endpoint: rpc,
        {BASE_RPC_ENV: "fixture://base"},
        changes.pop("clock", lambda: NOW),
        **changes,
    )


@pytest.mark.parametrize("profile_type", ["mnemonic", "raw_private_key"])
def test_exact_transaction_is_signed_and_broadcast_once(tmp_path, profile_type) -> None:
    repository, history, action, password, secret_canary, rpc = prepared_fixture(
        tmp_path, profile_type,
    )

    result = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
    )
    mapped = mainnet_result_to_map(result)

    assert result.code is MainnetTransferCode.PENDING
    assert result.broadcast_attempted and rpc.send_calls == 1
    assert result.transaction_hash.startswith("0x")
    assert result.recovered_signer == action.sender
    assert history.load()[0].status is HistoryStatus.PENDING
    assert history.load()[0].transaction_hash == result.transaction_hash
    assert mapped["canCheckStatus"]
    assert "raw" not in repr(mapped).lower()
    assert password not in repr(result)
    assert secret_canary not in repr(result)


def test_runtime_policy_requires_explicit_enable_and_fee_cap(tmp_path) -> None:
    _repository, _history, action, _password, _secret, _rpc = prepared_fixture(tmp_path)
    disabled = MainnetBroadcastPolicy.from_environment({})
    missing_fee = MainnetBroadcastPolicy.from_environment(
        {BROADCAST_ENABLED_ENV: "1"},
    )
    available = MainnetBroadcastPolicy.from_environment(
        {
            BROADCAST_ENABLED_ENV: "1",
            FEE_LIMIT_ENV: str(action.max_total_fee_wei),
        },
    )

    assert not disabled.available and not missing_fee.available
    assert available.available and available.evaluate(action) is None
    assert MainnetBroadcastPolicy(
        True, OfflineSigningPolicy(action.max_total_fee_wei - 1),
    ).evaluate(action) is MainnetTransferCode.FEE_LIMIT_EXCEEDED


@pytest.mark.parametrize(
    "changes",
    [
        {"chain_id": 1},
        {"decimals": 18},
        {"token_balance": 999_999},
        {"native_balance": 1},
        {"nonce": 8},
        {"gas": 50_001},
        {"priority_fee": 3},
        {"block": (123_457, 11)},
        {"block": (123_455, 10)},
        {"block": (123_456, 0)},
    ],
)
def test_final_live_revalidation_fails_closed_before_authentication(
    tmp_path, changes, monkeypatch,
) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    rpc.values.update(changes)

    def forbidden_authentication(*_args):
        raise AssertionError("Final revalidation reached vault authentication")

    monkeypatch.setattr(repository, "_authenticate_profile", forbidden_authentication)
    result = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
    )

    assert result.code is MainnetTransferCode.REVALIDATION_FAILED
    assert rpc.send_calls == 0
    assert history.load()[0].status is HistoryStatus.PREPARED


def test_history_hash_gate_blocks_broadcast_on_atomic_failure(
    tmp_path, monkeypatch,
) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)

    def failed_update(*_args, **_kwargs):
        raise StorageError("canary raw provider failure")

    monkeypatch.setattr(history, "update_status", failed_update)
    result = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
    )

    assert result.code is MainnetTransferCode.HISTORY_UNAVAILABLE
    assert not result.broadcast_attempted and rpc.send_calls == 0
    assert history.load()[0].status is HistoryStatus.PREPARED
    assert "canary" not in repr(result).lower()


@pytest.mark.parametrize("mode", ["transport", "hash_mismatch"])
def test_ambiguous_submission_is_unknown_and_never_retried(tmp_path, mode) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    if mode == "transport":
        rpc.values["send_error"] = TimeoutError("raw provider response")
    else:
        rpc.values["remote_hash"] = "0x" + "99" * 32

    result = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
    )

    assert result.code is MainnetTransferCode.UNKNOWN
    assert result.broadcast_attempted and rpc.send_calls == 1
    assert history.load()[0].status is HistoryStatus.UNKNOWN
    assert history.load()[0].transaction_hash == result.transaction_hash


def receipt(action, transaction_hash: str, status: int = 1, amount: int = 1_000_000):
    sender_topic = "0x" + action.sender[2:].lower().rjust(64, "0")
    recipient_topic = "0x" + action.recipient[2:].lower().rjust(64, "0")
    return {
        "transactionHash": transaction_hash,
        "from": action.sender,
        "to": action.token_contract,
        "status": status,
        "gasUsed": 45_000,
        "effectiveGasPrice": 12,
        "logs": [
            {
                "address": action.token_contract,
                "topics": [TRANSFER_EVENT_TOPIC, sender_topic, recipient_topic],
                "data": "0x" + amount.to_bytes(32, "big").hex(),
            },
        ],
    }


def test_receipt_tracker_confirms_exact_usdc_event_and_marks_revert(tmp_path) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    sent = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
    )
    tracker = BroadcastReceiptTracker(
        history,
        lambda _endpoint: rpc,
        {BASE_RPC_ENV: "fixture://base"},
        lambda: NOW,
        timeout_seconds=0,
    )
    rpc.values["receipt"] = receipt(action, sent.transaction_hash)
    confirmed = tracker.check_once(action.action_id)

    assert confirmed.status is HistoryStatus.CONFIRMED
    assert history.load()[0].status is HistoryStatus.CONFIRMED
    assert history.load()[0].actual_fee_wei == "540000"

    second_dir = tmp_path / "reverted"
    repository2, history2, action2, password2, _secret2, rpc2 = prepared_fixture(second_dir)
    sent2 = executor(repository2, history2, rpc2).execute(
        action2, action2.digest, password2, SigningPermit(),
    )
    rpc2.values["receipt"] = receipt(action2, sent2.transaction_hash, status=0)
    reverted = BroadcastReceiptTracker(
        history2,
        lambda _endpoint: rpc2,
        {BASE_RPC_ENV: "fixture://base"},
        lambda: NOW,
        timeout_seconds=0,
    ).check_once(action2.action_id)

    assert reverted.status is HistoryStatus.FAILED
    assert history2.load()[0].status is HistoryStatus.FAILED
    assert history2.load()[0].actual_fee_wei == "540000"


def test_receipt_tracker_rejects_wrong_event_and_recovers_unknown_pending(
    tmp_path,
) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    sent = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
    )
    history.update_status(
        action.action_id, HistoryStatus.UNKNOWN, "2026-07-21T12:01:00Z",
        sent.transaction_hash,
    )
    tracker = BroadcastReceiptTracker(
        history,
        lambda _endpoint: rpc,
        {BASE_RPC_ENV: "fixture://base"},
        lambda: NOW,
        timeout_seconds=0,
    )
    rpc.values["transaction"] = {
        "hash": sent.transaction_hash,
        "from": action.sender,
        "to": action.token_contract,
    }
    chain_calls = rpc.chain_calls
    assert tracker.check_once(action.action_id).status is HistoryStatus.PENDING
    assert rpc.chain_calls == chain_calls

    rpc.values["receipt"] = receipt(action, sent.transaction_hash, amount=2_000_000)
    assert tracker.check_once(action.action_id).status is HistoryStatus.UNKNOWN
    assert rpc.chain_calls == chain_calls

    malformed_fee = receipt(action, sent.transaction_hash)
    malformed_fee.pop("effectiveGasPrice")
    rpc.values["receipt"] = malformed_fee
    assert tracker.check_once(action.action_id).status is HistoryStatus.UNKNOWN
    assert history.load()[0].actual_fee_wei is None


def test_tracking_timeout_is_read_only_and_keeps_accepted_submission_pending(
    tmp_path,
) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
    )
    monotonic = [0.0]

    def sleep(seconds: float) -> None:
        monotonic[0] += seconds

    result = BroadcastReceiptTracker(
        history,
        lambda _endpoint: rpc,
        {BASE_RPC_ENV: "fixture://base"},
        lambda: NOW,
        lambda: monotonic[0],
        sleep,
        timeout_seconds=6,
        poll_interval_seconds=3,
    ).track(action.action_id)

    assert result.status is HistoryStatus.PENDING
    assert rpc.receipt_calls == 3
    assert rpc.send_calls == 1
