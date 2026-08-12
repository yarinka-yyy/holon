from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from web3 import Web3
from web3.exceptions import Web3RPCError
from holon_policy import Policy, RecipientRule, TransferRule

from holon_wallet.broadcast import (
    ALCHEMY_BASE_RPC_URL,
    BASE_RPC_ENV,
    BROADCAST_ENABLED_ENV,
    DEFAULT_BASE_RPC_URL,
    TRANSFER_EVENT_TOPIC,
    BroadcastReceiptTracker,
    MainnetBroadcastPolicy,
    MainnetTransferCode,
    MainnetTransferExecutor,
    MainnetTransferResult,
    ReceiptTrackingCode,
    SubmissionRejectedError,
    Web3MainnetRpc,
    _endpoint,
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
    TransferPreflightError,
    TransferPreflightService,
    transfer_route,
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
            "receipt_error": None,
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
        if self.values["receipt_error"] is not None:
            raise self.values["receipt_error"]
        return self.values["receipt"]


class ReadUnavailableRpc(MainnetRpcStub):
    def chain_id(self):
        raise RuntimeError("read-only provider unavailable")


def new_password() -> str:
    return secrets.token_urlsafe(18)


def new_raw_key():
    while True:
        try:
            return import_private_key(secrets.token_hex(32))
        except ValueError:
            continue


def prepared_fixture(
    tmp_path,
    profile_type: str = "mnemonic",
    network_id: str = "base",
    asset_id: str = "usdc",
    amount_atomic: int = 1_000_000,
):
    password = new_password()
    repository = VaultRepository(WalletPaths(tmp_path))
    material = generate_mnemonic() if profile_type == "mnemonic" else new_raw_key()
    record = repository.new_record(material, "Main Account")
    repository.create_new(password, record)
    route = transfer_route(network_id, asset_id)
    rpc = MainnetRpcStub(chain_id=route.chain_id, token_balance=10_000_000)
    request = PendingTransferRequest(
        "act-mainnet",
        record.summary.profile_id,
        NOW,
        NOW + timedelta(minutes=5),
        network_id,
        asset_id,
        amount_atomic,
    )
    action = TransferPreflightService(
        lambda _endpoint: rpc, environ={route.endpoint_env: "fixture://route"},
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
        {
            BASE_RPC_ENV: "fixture://base",
            "HOLON_ETHEREUM_RPC_URL": "fixture://ethereum",
        },
        changes.pop("clock", lambda: NOW),
        **changes,
    )


def shared_policy(
    recipient: str,
    amount_cap: int,
    recipient_cap: int,
    fee_cap: int,
) -> MainnetBroadcastPolicy:
    return MainnetBroadcastPolicy.from_policy(Policy(
        "2",
        "1",
        True,
        (TransferRule(
            "base",
            "usdc",
            8453,
            str(amount_cap),
            str(fee_cap),
            (RecipientRule(recipient.lower(), str(recipient_cap)),),
        ),),
    ))


def test_shared_policy_enforces_recipient_caps_fee_and_version(tmp_path) -> None:
    repository, _history, action, _password, _secret, _rpc = prepared_fixture(
        tmp_path, amount_atomic=750_000,
    )
    del repository
    policy = shared_policy(
        RECIPIENT, 1_000_000, 750_000, action.max_total_fee_wei,
    )

    assert policy.draft_amount_code(
        "base", "usdc", 750_000, RECIPIENT, "1",
    ) is None
    assert policy.maximum_draft_amount(
        "base", "usdc", 2_000_000, RECIPIENT,
    ) == 750_000
    assert policy.draft_amount_code(
        "base", "usdc", 750_001, RECIPIENT,
    ) is MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED
    assert policy.draft_amount_code(
        "base", "usdc", 1, "0x" + "55" * 20,
    ) is MainnetTransferCode.RECIPIENT_NOT_ALLOWED
    assert policy.draft_amount_code(
        "base", "usdc", 1, RECIPIENT, "2",
    ) is MainnetTransferCode.POLICY_VERSION_MISMATCH
    assert policy.evaluate(action) is None
    assert policy.evaluate(replace(
        action, max_total_fee_wei=action.max_total_fee_wei + 1,
    )) is MainnetTransferCode.FEE_LIMIT_EXCEEDED
    assert policy.evaluate(replace(
        action, recipient="0x" + "55" * 20,
    )) is MainnetTransferCode.RECIPIENT_NOT_ALLOWED


def test_transfer_environment_cannot_enable_default_executor(tmp_path) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    route = transfer_route(action.network_id, action.asset_id)
    configured = {
        BROADCAST_ENABLED_ENV: "1",
        FEE_LIMIT_ENV: str(10**18),
        route.amount_cap_env: str(10**18),
        BASE_RPC_ENV: "fixture://base",
    }
    item = MainnetTransferExecutor(
        repository,
        history,
        rpc_factory=lambda _endpoint: rpc,
        environ=configured,
        clock=lambda: NOW,
    )
    chain_calls_before = rpc.chain_calls

    result = item.execute(action, action.digest, password, SigningPermit())

    assert result.code is MainnetTransferCode.POLICY_UNAVAILABLE
    assert not result.broadcast_attempted
    assert rpc.chain_calls == chain_calls_before
    assert rpc.send_calls == 0


def test_network_owned_endpoint_keeps_arbitrum_usdc_without_eth_route() -> None:
    assert _endpoint({}, "arbitrum") == "https://arb1.arbitrum.io/rpc"
    assert _endpoint({"HOLON_ARBITRUM_RPC_URL": "fixture://arbitrum"}, "arbitrum") == (
        "fixture://arbitrum"
    )
    with pytest.raises(TransferPreflightError):
        transfer_route("arbitrum", "eth")


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


@pytest.mark.parametrize(
    ("action_type", "title"),
    [
        ("lending_approve", "Aave approval confirmed"),
        ("lending_supply", "Supplied to Aave V3"),
        ("lending_withdraw", "Withdrawn from Aave V3"),
        ("lending_withdraw_all", "Withdrawn from Aave V3"),
        ("transfer", "Transfer confirmed"),
        ("revoke", "Approval revoked"),
    ],
)
def test_result_copy_is_action_aware(action_type: str, title: str) -> None:
    result = MainnetTransferResult(
        code=MainnetTransferCode.CONFIRMED,
        action_id="act-result",
        digest="a" * 64,
        transaction_hash="0x" + "b" * 64,
        recovered_signer="0x" + "11" * 20,
        history_status=HistoryStatus.CONFIRMED,
        completed_at="2026-07-26T12:00:00Z",
        broadcast_attempted=True,
        history_available=True,
        simulation=False,
        action_type=action_type,
    )

    assert mainnet_result_to_map(result)["title"] == title


def test_ethereum_native_uses_ethereum_endpoint_and_exact_receipt(tmp_path) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(
        tmp_path, network_id="ethereum", asset_id="eth", amount_atomic=10**15,
    )
    endpoints: list[str] = []

    def factory(endpoint: str):
        endpoints.append(endpoint)
        return rpc

    environ = {
        BASE_RPC_ENV: "fixture://base",
        "HOLON_ETHEREUM_RPC_URL": "fixture://ethereum",
        "HOLON_ETHEREUM_BROADCAST_ENABLED": "1",
        "HOLON_ETHEREUM_MAX_TOTAL_FEE_WEI": str(action.max_total_fee_wei),
        "HOLON_ETHEREUM_ETH_MAX_AMOUNT_WEI": str(action.amount_atomic),
    }
    sender = MainnetTransferExecutor(
        repository,
        history,
        MainnetBroadcastPolicy.from_environment(environ),
        factory,
        environ,
        lambda: NOW,
    )
    sent = sender.execute(action, action.digest, password, SigningPermit())
    assert sent.code is MainnetTransferCode.PENDING
    assert endpoints == ["fixture://ethereum"]
    assert rpc.send_calls == 1

    rpc.values["transaction"] = {
        "hash": sent.transaction_hash,
        "from": action.sender,
        "to": action.recipient,
        "value": action.amount_atomic,
        "input": "0x",
        "chainId": action.chain_id,
    }
    rpc.values["receipt"] = {
        "transactionHash": sent.transaction_hash,
        "from": action.sender,
        "to": action.recipient,
        "status": 1,
        "gasUsed": 21_000,
        "effectiveGasPrice": 12,
        "logs": [],
    }
    confirmed = BroadcastReceiptTracker(
        history, factory, environ, lambda: NOW, timeout_seconds=0,
    ).check_once(action.action_id)
    assert confirmed.status is HistoryStatus.CONFIRMED
    assert endpoints[-1] == "fixture://ethereum"


def test_runtime_policy_requires_explicit_enable_fee_and_amount_caps(tmp_path) -> None:
    _repository, _history, action, _password, _secret, _rpc = prepared_fixture(tmp_path)
    disabled = MainnetBroadcastPolicy.from_environment({})
    missing_fee = MainnetBroadcastPolicy.from_environment(
        {BROADCAST_ENABLED_ENV: "1"},
    )
    missing_amount = MainnetBroadcastPolicy.from_environment(
        {
            BROADCAST_ENABLED_ENV: "1",
            FEE_LIMIT_ENV: str(action.max_total_fee_wei),
        },
    )
    available = MainnetBroadcastPolicy.from_environment(
        {
            BROADCAST_ENABLED_ENV: "1",
            FEE_LIMIT_ENV: str(action.max_total_fee_wei),
            "HOLON_BASE_USDC_MAX_AMOUNT_ATOMIC": str(action.amount_atomic),
        },
    )

    assert not disabled.available and not missing_fee.available
    assert not missing_amount.available
    assert available.available and available.evaluate(action) is None
    assert MainnetBroadcastPolicy(
        True, OfflineSigningPolicy(action.max_total_fee_wei - 1),
    ).evaluate(action) is MainnetTransferCode.FEE_LIMIT_EXCEEDED
    limited = MainnetBroadcastPolicy.from_environment(
        {
            BROADCAST_ENABLED_ENV: "1",
            FEE_LIMIT_ENV: str(action.max_total_fee_wei),
            "HOLON_BASE_USDC_MAX_AMOUNT_ATOMIC": str(action.amount_atomic - 1),
        },
    )
    assert limited.evaluate(action) is MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED


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


def test_readonly_revalidation_falls_back_but_broadcasts_primary_once(tmp_path) -> None:
    repository, history, action, password, _secret, _rpc = prepared_fixture(tmp_path)
    primary = ReadUnavailableRpc()
    fallback = MainnetRpcStub()
    endpoints = {
        "fixture://primary": primary,
        DEFAULT_BASE_RPC_URL: fallback,
        ALCHEMY_BASE_RPC_URL: fallback,
    }
    result = MainnetTransferExecutor(
        repository, history, MainnetBroadcastPolicy(True, OfflineSigningPolicy(10**18)),
        endpoints.__getitem__, {BASE_RPC_ENV: "fixture://primary"}, lambda: NOW,
    ).execute(action, action.digest, password, SigningPermit())

    assert result.code is MainnetTransferCode.PENDING
    assert primary.send_calls == 1
    assert fallback.send_calls == 0


def test_revalidation_semantic_failure_does_not_fall_back_or_authenticate(
    tmp_path, monkeypatch,
) -> None:
    repository, history, action, password, _secret, _rpc = prepared_fixture(tmp_path)
    primary = MainnetRpcStub(chain_id=1)
    fallback = MainnetRpcStub()
    endpoints = {
        "fixture://primary": primary,
        DEFAULT_BASE_RPC_URL: fallback,
        ALCHEMY_BASE_RPC_URL: fallback,
    }

    def forbidden_authentication(*_args):
        raise AssertionError("Final revalidation reached vault authentication")

    monkeypatch.setattr(repository, "_authenticate_profile", forbidden_authentication)
    result = MainnetTransferExecutor(
        repository, history, MainnetBroadcastPolicy(True, OfflineSigningPolicy(10**18)),
        endpoints.__getitem__, {BASE_RPC_ENV: "fixture://primary"}, lambda: NOW,
    ).execute(action, action.digest, password, SigningPermit())

    assert result.code is MainnetTransferCode.REVALIDATION_FAILED
    assert primary.send_calls == fallback.send_calls == 0
    assert fallback.chain_calls == 0


def test_revalidation_provider_exhaustion_fails_before_authentication(
    tmp_path, monkeypatch,
) -> None:
    repository, history, action, password, _secret, _rpc = prepared_fixture(tmp_path)
    unavailable = ReadUnavailableRpc()

    def forbidden_authentication(*_args):
        raise AssertionError("Final revalidation reached vault authentication")

    monkeypatch.setattr(repository, "_authenticate_profile", forbidden_authentication)
    result = MainnetTransferExecutor(
        repository, history, MainnetBroadcastPolicy(True, OfflineSigningPolicy(10**18)),
        lambda _endpoint: unavailable, {BASE_RPC_ENV: "fixture://primary"}, lambda: NOW,
    ).execute(action, action.digest, password, SigningPermit())

    assert result.code is MainnetTransferCode.REVALIDATION_RPC_UNAVAILABLE
    assert unavailable.send_calls == 0


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


def test_external_attempt_marker_is_immediately_before_send(tmp_path) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    events: list[str] = []

    def mark_attempt() -> None:
        assert rpc.send_calls == 0
        events.append("marked")

    original_send = rpc.send_raw_transaction

    def send(raw_transaction):
        assert events == ["marked"]
        return original_send(raw_transaction)

    rpc.send_raw_transaction = send
    result = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
        on_broadcast_starting=mark_attempt,
    )

    assert result.code is MainnetTransferCode.PENDING
    assert result.broadcast_attempted and rpc.send_calls == 1


def test_external_attempt_marker_failure_prevents_send(tmp_path) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)

    def fail_marker() -> None:
        raise StorageError("private marker detail")

    result = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
        on_broadcast_starting=fail_marker,
    )

    assert result.code is MainnetTransferCode.HISTORY_UNAVAILABLE
    assert not result.broadcast_attempted and rpc.send_calls == 0
    assert "private marker detail" not in repr(result)


def test_final_expiry_gate_blocks_send_after_history_persistence(
    tmp_path, monkeypatch,
) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    expired = False
    original_update = history.update_status

    def update_then_expire(*args, **kwargs):
        nonlocal expired
        result = original_update(*args, **kwargs)
        expired = True
        return result

    monkeypatch.setattr(history, "update_status", update_then_expire)
    result = executor(
        repository,
        history,
        rpc,
        clock=lambda: action.expires_at if expired else NOW,
    ).execute(action, action.digest, password, SigningPermit())

    assert result.code is MainnetTransferCode.ACTION_EXPIRED
    assert not result.broadcast_attempted
    assert rpc.send_calls == 0


def test_final_cancel_gate_blocks_send_after_policy_evaluation(
    tmp_path, monkeypatch,
) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    permit = SigningPermit()
    from holon_wallet import broadcast as broadcast_module

    original_evaluate = broadcast_module._evaluate_policy
    evaluations = 0

    def cancel_on_final_evaluation(policy, revoke_policy, candidate):
        nonlocal evaluations
        evaluations += 1
        result = original_evaluate(policy, revoke_policy, candidate)
        if evaluations == 3:
            permit.cancel()
        return result

    monkeypatch.setattr(
        broadcast_module, "_evaluate_policy", cancel_on_final_evaluation,
    )
    result = executor(repository, history, rpc).execute(
        action, action.digest, password, permit,
    )

    assert evaluations == 3
    assert result.code is MainnetTransferCode.CANCELLED
    assert not result.broadcast_attempted
    assert rpc.send_calls == 0


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


def test_definite_submission_rejection_is_not_masked_as_unknown(tmp_path) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    rpc.values["send_error"] = Web3RPCError("private RPC response detail")

    result = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
    )

    assert result.code is MainnetTransferCode.SUBMISSION_REJECTED
    assert result.broadcast_attempted and rpc.send_calls == 1
    assert history.load()[0].status is HistoryStatus.FAILED
    mapped = mainnet_result_to_map(result)
    assert mapped["title"] == "Submission rejected"
    assert mapped["canCheckStatus"] is False
    assert "private RPC response" not in mapped["message"]


def test_web3_submission_rejection_keeps_provider_detail_internal() -> None:
    class RejectingEth:
        def send_raw_transaction(self, _raw_transaction):
            raise Web3RPCError("provider detail must not reach Wallet")

    rpc = object.__new__(Web3MainnetRpc)
    rpc._web3 = SimpleNamespace(eth=RejectingEth())

    with pytest.raises(SubmissionRejectedError) as error:
        rpc.send_raw_transaction(b"signed-transaction")

    assert "provider detail" not in str(error.value)


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
        "value": 0,
        "input": action.transaction.data,
        "chainId": action.chain_id,
    }
    chain_calls = rpc.chain_calls
    assert tracker.check_once(action.action_id).status is HistoryStatus.PENDING
    assert rpc.chain_calls == chain_calls + 3

    rpc.values["receipt"] = receipt(action, sent.transaction_hash, amount=2_000_000)
    assert tracker.check_once(action.action_id).status is HistoryStatus.UNKNOWN
    assert rpc.chain_calls == chain_calls + 4

    malformed_fee = receipt(action, sent.transaction_hash)
    malformed_fee.pop("effectiveGasPrice")
    rpc.values["receipt"] = malformed_fee
    assert tracker.check_once(action.action_id).status is HistoryStatus.UNKNOWN
    assert history.load()[0].actual_fee_wei is None


def test_tracking_timeout_is_read_only_and_defers_as_unknown(
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

    assert result.status is HistoryStatus.UNKNOWN
    assert result.code is ReceiptTrackingCode.TRANSACTION_UNKNOWN
    assert history.load()[0].status is HistoryStatus.UNKNOWN
    assert rpc.receipt_calls == 9
    assert rpc.send_calls == 1


def test_receipt_rpc_failure_becomes_unknown_without_rebroadcast(tmp_path) -> None:
    repository, history, action, password, _secret, rpc = prepared_fixture(tmp_path)
    sent = executor(repository, history, rpc).execute(
        action, action.digest, password, SigningPermit(),
    )
    rpc.values["receipt_error"] = TimeoutError("provider canary must not persist")

    tracked = BroadcastReceiptTracker(
        history,
        lambda _endpoint: rpc,
        {BASE_RPC_ENV: "fixture://configured"},
        lambda: NOW,
        timeout_seconds=0,
    ).check_once(action.action_id)

    assert tracked.status is HistoryStatus.UNKNOWN
    assert tracked.code is ReceiptTrackingCode.RPC_UNAVAILABLE
    assert tracked.endpoint_class == "alchemy_public"
    stored = history.load()[0]
    assert stored.status is HistoryStatus.UNKNOWN
    assert stored.receipt_code == "RECEIPT_RPC_UNAVAILABLE"
    assert stored.receipt_endpoint_class == "alchemy_public"
    assert "provider canary" not in history.path.read_text(encoding="utf-8")
    assert rpc.send_calls == 1


def test_receipt_pool_uses_validated_official_fallback(tmp_path) -> None:
    repository, history, action, password, _secret, primary = prepared_fixture(tmp_path)
    sent = executor(repository, history, primary).execute(
        action, action.digest, password, SigningPermit(),
    )
    configured = MainnetRpcStub(
        receipt_error=RuntimeError("configured endpoint unavailable"),
    )
    official = MainnetRpcStub(receipt=receipt(action, sent.transaction_hash))
    alchemy = MainnetRpcStub(receipt_error=AssertionError("fallback must stop"))
    endpoints = {
        "fixture://configured": configured,
        DEFAULT_BASE_RPC_URL: official,
        ALCHEMY_BASE_RPC_URL: alchemy,
    }

    tracked = BroadcastReceiptTracker(
        history,
        endpoints.__getitem__,
        {BASE_RPC_ENV: "fixture://configured"},
        lambda: NOW,
        timeout_seconds=0,
    ).check_once(action.action_id)

    assert tracked.status is HistoryStatus.CONFIRMED
    assert tracked.code is ReceiptTrackingCode.CONFIRMED
    assert tracked.endpoint_class == "official"
    assert configured.receipt_calls == 1
    assert official.receipt_calls == 1
    assert alchemy.receipt_calls == 0
    assert primary.send_calls == 1
    assert configured.send_calls == official.send_calls == alchemy.send_calls == 0
    stored = history.load()[0]
    assert stored.receipt_code == "RECEIPT_CONFIRMED"
    assert stored.receipt_endpoint_class == "official"


@pytest.mark.parametrize(
    ("configured_chain", "official_chain", "alchemy_chain", "expected"),
    [
        (1, 1, 1, ReceiptTrackingCode.WRONG_CHAIN),
        (8453, 1, 1, ReceiptTrackingCode.VALIDATION_FAILED),
    ],
)
def test_receipt_pool_fails_closed_for_wrong_chain_or_malformed_receipt(
    tmp_path, configured_chain, official_chain, alchemy_chain, expected,
) -> None:
    repository, history, action, password, _secret, primary = prepared_fixture(tmp_path)
    sent = executor(repository, history, primary).execute(
        action, action.digest, password, SigningPermit(),
    )
    malformed = receipt(action, sent.transaction_hash)
    malformed["transactionHash"] = "0x" + "99" * 32
    configured = MainnetRpcStub(
        chain_id=configured_chain,
        receipt=malformed if configured_chain == 8453 else None,
    )
    official = MainnetRpcStub(chain_id=official_chain)
    alchemy = MainnetRpcStub(chain_id=alchemy_chain)
    endpoints = {
        "fixture://configured": configured,
        DEFAULT_BASE_RPC_URL: official,
        ALCHEMY_BASE_RPC_URL: alchemy,
    }

    tracked = BroadcastReceiptTracker(
        history,
        endpoints.__getitem__,
        {BASE_RPC_ENV: "fixture://configured"},
        lambda: NOW,
        timeout_seconds=0,
    ).check_once(action.action_id)

    assert tracked.status is HistoryStatus.UNKNOWN
    assert tracked.code is expected
    assert primary.send_calls == 1
    assert configured.send_calls == official.send_calls == alchemy.send_calls == 0
