from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from requests import exceptions as request_errors
from web3 import Web3

from holon_wallet.approval import (
    APPROVAL_ROUTES,
    AllowanceReadService,
    AllowanceStatus,
    RevokeFlowCoordinator,
    RevokeFlowError,
    RevokePolicy,
    RevokePreflightCode,
    RevokePreflightError,
    RevokePreflightService,
    UINT256_MAX,
    allowance_snapshot_to_map,
    encode_usdc_approve_zero,
    format_allowance,
)
from holon_wallet.broadcast import (
    APPROVAL_EVENT_TOPIC,
    BroadcastReceiptTracker,
    MainnetBroadcastPolicy,
    MainnetTransferCode,
    MainnetTransferExecutor,
    _receipt_status,
)
from holon_wallet.history import HistoryStatus, HistoryStore, WalletHistoryRecord
from holon_wallet.signer import (
    OfflineSigningPolicy,
    decoded_transaction_matches,
    transaction_dict,
    validate_signing_action,
)
from holon_wallet.storage import WalletPaths
from holon_wallet.transfer import SigningPermit
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import import_private_key

SPENDER = Web3.to_checksum_address("0x1234567890abcdef1234567890abcdef12345678")
OTHER_SPENDER = Web3.to_checksum_address("0xabcdef1234567890abcdef1234567890abcdef12")


def policy_env(*, enabled: bool = True, network_id: str = "base") -> dict[str, str]:
    route = APPROVAL_ROUTES[network_id]
    return {
        route.enabled_env: "1" if enabled else "0",
        route.spender_env: SPENDER,
        route.fee_cap_env: str(10**18),
        route.endpoint_env: f"fixture://{network_id}",
    }


class ApprovalRpcStub:
    def __init__(self, network_id: str = "base", allowance: int = 2_500_000) -> None:
        self.route = APPROVAL_ROUTES[network_id]
        self.observed_chain_id = self.route.chain_id
        self.allowance_value = allowance
        self.decimals = 6
        self.native = 10**18
        self.nonce = 7
        self.block = 1234
        self.base_fee = 10
        self.priority_fee = 2
        self.gas = 50_000
        self.estimated_transaction = None
        self.send_calls = 0
        self.receipt = None
        self.public_transaction = None

    def chain_id(self):
        return self.observed_chain_id

    def latest_block(self):
        return self.block, self.base_fee

    def native_balance(self, _address):
        return self.native

    def token_decimals(self, _contract):
        return self.decimals

    def token_balance(self, _contract, _address):
        return 0

    def allowance(self, _contract, _owner, _spender):
        return self.allowance_value

    def pending_nonce(self, _address):
        return self.nonce

    def max_priority_fee_per_gas(self):
        return self.priority_fee

    def estimate_gas(self, transaction):
        self.estimated_transaction = dict(transaction)
        return self.gas

    def send_raw_transaction(self, raw_transaction):
        self.send_calls += 1
        return Web3.to_hex(Web3.keccak(raw_transaction))

    def transaction(self, _transaction_hash):
        return self.public_transaction

    def transaction_receipt(self, _transaction_hash):
        return self.receipt


def profile(repository: VaultRepository | None = None, password: str | None = None):
    secret = import_private_key(secrets.token_hex(32))
    if repository is None:
        repository = VaultRepository(WalletPaths())
    record = repository.new_record(secret, "Approval Account")
    if password is not None:
        repository.create_new(password, record)
    return record.summary


@pytest.mark.parametrize("network_id", ["ethereum", "base"])
def test_policy_and_preflight_build_exact_revoke(network_id, tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path / network_id))
    summary = profile(repository)
    environ = policy_env(network_id=network_id)
    policy = RevokePolicy.from_environment(environ)
    rpc = ApprovalRpcStub(network_id)
    service = RevokePreflightService(policy, lambda _endpoint: rpc, environ)
    flow = RevokeFlowCoordinator(action_id_factory=lambda: f"act-{network_id}")
    request = flow.begin(summary.profile_id, network_id)
    action = service.prepare(request, summary)

    route = APPROVAL_ROUTES[network_id]
    assert flow.accept(action)
    assert action.action_type == "revoke"
    assert action.token_contract == route.token_contract
    assert action.spender == SPENDER
    assert action.allowance_before_atomic == 2_500_000
    assert action.new_allowance_atomic == 0
    assert action.transaction.to == route.token_contract
    assert action.transaction.value == 0
    assert action.transaction.data == encode_usdc_approve_zero(SPENDER)
    assert rpc.estimated_transaction["data"] == action.transaction.data
    assert validate_signing_action(action, action.digest, action.created_at) is None
    assert transaction_dict(action)["to"] == route.token_contract


def test_policy_is_exact_and_read_only_inspection_does_not_need_enable(tmp_path) -> None:
    summary = profile(VaultRepository(WalletPaths(tmp_path)))
    environ = policy_env(enabled=False)
    policy = RevokePolicy.from_environment(environ)
    rpc = ApprovalRpcStub()
    snapshots = AllowanceReadService(
        policy, lambda _endpoint: rpc, environ,
    ).inspect_all(summary)
    base = next(item for item in snapshots if item.network_id == "base")
    ethereum = next(item for item in snapshots if item.network_id == "ethereum")
    assert base.status is AllowanceStatus.LIVE
    assert not allowance_snapshot_to_map(base, policy)["revokeAvailable"]
    assert ethereum.status is AllowanceStatus.NOT_CONFIGURED

    invalid = dict(environ)
    invalid[APPROVAL_ROUTES["base"].spender_env] = SPENDER.lower()
    assert RevokePolicy.from_environment(invalid).spenders["base"] is None
    invalid[APPROVAL_ROUTES["base"].spender_env] = APPROVAL_ROUTES["base"].token_contract
    assert RevokePolicy.from_environment(invalid).spenders["base"] is None
    invalid[APPROVAL_ROUTES["base"].fee_cap_env] = "01"
    assert RevokePolicy.from_environment(invalid).fee_caps["base"] is None
    invalid = dict(environ)
    invalid[APPROVAL_ROUTES["base"].enabled_env] = " 1"
    invalid[APPROVAL_ROUTES["base"].spender_env] = SPENDER + " "
    invalid[APPROVAL_ROUTES["base"].fee_cap_env] = "1 "
    whitespace = RevokePolicy.from_environment(invalid)
    assert not whitespace.enabled["base"]
    assert whitespace.spenders["base"] is None
    assert whitespace.fee_caps["base"] is None


def test_allowance_formatting_and_zero_refusal(tmp_path) -> None:
    assert format_allowance(0) == "0 USDC"
    assert format_allowance(1) == "0.000001 USDC"
    assert format_allowance(2_500_000) == "2.5 USDC"
    assert format_allowance(UINT256_MAX) == "Unlimited USDC"

    summary = profile(VaultRepository(WalletPaths(tmp_path)))
    environ = policy_env()
    rpc = ApprovalRpcStub(allowance=0)
    service = RevokePreflightService(
        RevokePolicy.from_environment(environ), lambda _endpoint: rpc, environ,
    )
    request = RevokeFlowCoordinator().begin(summary.profile_id, "base")
    with pytest.raises(RevokePreflightError) as captured:
        service.prepare(request, summary)
    assert captured.value.code is RevokePreflightCode.NO_ACTIVE_ALLOWANCE


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("chain", RevokePreflightCode.WRONG_CHAIN),
        ("decimals", RevokePreflightCode.TOKEN_METADATA_INVALID),
        ("native", RevokePreflightCode.INSUFFICIENT_ETH),
        ("fee", RevokePreflightCode.FEE_LIMIT_EXCEEDED),
    ],
)
def test_preflight_refusals_are_route_specific(tmp_path, mutation, expected) -> None:
    summary = profile(VaultRepository(WalletPaths(tmp_path)))
    environ = policy_env()
    rpc = ApprovalRpcStub()
    if mutation == "chain":
        rpc.observed_chain_id = 1
    elif mutation == "decimals":
        rpc.decimals = 18
    elif mutation == "native":
        rpc.native = 1
    elif mutation == "fee":
        environ[APPROVAL_ROUTES["base"].fee_cap_env] = "1"
    service = RevokePreflightService(
        RevokePolicy.from_environment(environ), lambda _endpoint: rpc, environ,
    )
    request = RevokeFlowCoordinator().begin(summary.profile_id, "base")
    with pytest.raises(RevokePreflightError) as captured:
        service.prepare(request, summary)
    assert captured.value.code is expected


def test_transport_retry_is_bounded_and_owner_cannot_be_spender(tmp_path) -> None:
    summary = profile(VaultRepository(WalletPaths(tmp_path)))
    environ = policy_env()
    calls = 0
    rpc = ApprovalRpcStub()

    def factory(_endpoint):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise request_errors.ConnectionError("fixture")
        return rpc

    snapshot = AllowanceReadService(
        RevokePolicy.from_environment(environ), factory, environ,
    ).inspect(summary, "base")
    assert snapshot.status is AllowanceStatus.LIVE
    assert calls == 2

    route = APPROVAL_ROUTES["base"]
    owner_env = dict(environ)
    owner_env[route.spender_env] = summary.address
    owner_policy = RevokePolicy.from_environment(owner_env)
    assert owner_policy.spender_for("base", summary.address) is None


def test_digest_and_decoded_transaction_detect_mutation(tmp_path) -> None:
    summary = profile(VaultRepository(WalletPaths(tmp_path)))
    environ = policy_env()
    rpc = ApprovalRpcStub()
    service = RevokePreflightService(
        RevokePolicy.from_environment(environ), lambda _endpoint: rpc, environ,
    )
    request = RevokeFlowCoordinator().begin(summary.profile_id, "base")
    action = service.prepare(request, summary)
    changed = replace(action, allowance_before_atomic=action.allowance_before_atomic + 1)
    assert changed.digest != action.digest
    decoded = {
        "type": 2,
        "chainId": action.chain_id,
        "nonce": action.transaction.nonce,
        "to": bytes.fromhex(action.transaction.to[2:]),
        "value": 0,
        "data": bytes.fromhex(action.transaction.data[2:]),
        "gas": action.transaction.gas,
        "maxFeePerGas": action.transaction.max_fee_per_gas,
        "maxPriorityFeePerGas": action.transaction.max_priority_fee_per_gas,
        "accessList": [],
    }
    assert decoded_transaction_matches(decoded, action)
    decoded["data"] = bytes.fromhex(encode_usdc_approve_zero(OTHER_SPENDER)[2:])
    assert not decoded_transaction_matches(decoded, action)


def test_revoke_authority_expires_rejects_replay_and_profile_change(tmp_path) -> None:
    now = datetime(2026, 7, 23, tzinfo=UTC)
    current = [now]
    ids = iter(("act-first", "act-second", "act-first"))
    flow = RevokeFlowCoordinator(
        clock=lambda: current[0], action_id_factory=lambda: next(ids),
    )
    summary = profile(VaultRepository(WalletPaths(tmp_path)))
    environ = policy_env()
    service = RevokePreflightService(
        RevokePolicy.from_environment(environ),
        lambda _endpoint: ApprovalRpcStub(),
        environ,
    )

    first = service.prepare(flow.begin(summary.profile_id, "base"), summary)
    assert flow.accept(first)
    current[0] += timedelta(minutes=5)
    assert flow.begin_execution(first.action_id, first.digest, summary.profile_id) is None
    assert flow.current is None

    second = service.prepare(flow.begin(summary.profile_id, "base"), summary)
    assert flow.accept(second)
    assert flow.profile_changed("different-profile")
    assert flow.current is None
    with pytest.raises(RevokeFlowError, match="Terminal action IDs cannot be reused"):
        flow.begin(summary.profile_id, "base")


def _history_record(action) -> WalletHistoryRecord:
    timestamp = action.created_at.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return WalletHistoryRecord(
        action.action_id, action.profile_id, "revoke", action.network_id,
        action.chain_id, action.sender, action.spender, action.token_contract,
        "USDC", "0", 6, None, HistoryStatus.PREPARED, timestamp, timestamp,
        False, str(action.max_total_fee_wei), None,
    )


def test_revalidation_signs_broadcasts_once_and_requires_exact_approval_event(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = secrets.token_urlsafe(18)
    summary = profile(repository, password)
    environ = policy_env()
    policy = RevokePolicy.from_environment(environ)
    rpc = ApprovalRpcStub()
    action = RevokePreflightService(
        policy, lambda _endpoint: rpc, environ,
    ).prepare(RevokeFlowCoordinator().begin(summary.profile_id, "base"), summary)
    history = HistoryStore(repository.paths)
    history.append(_history_record(action))
    executor = MainnetTransferExecutor(
        repository,
        history,
        MainnetBroadcastPolicy(False, OfflineSigningPolicy(None)),
        lambda _endpoint: rpc,
        environ,
        revoke_policy=policy,
    )
    result = executor.execute(action, action.digest, password, SigningPermit())
    assert result.code is MainnetTransferCode.PENDING
    assert result.action_type == "revoke"
    assert result.broadcast_attempted and rpc.send_calls == 1

    tx_hash = result.transaction_hash
    rpc.public_transaction = {
        "hash": tx_hash,
        "from": action.sender,
        "to": action.token_contract,
        "value": 0,
        "input": action.transaction.data,
        "chainId": action.chain_id,
    }
    rpc.receipt = {
        "transactionHash": tx_hash,
        "from": action.sender,
        "to": action.token_contract,
        "status": 1,
        "gasUsed": action.transaction.gas,
        "effectiveGasPrice": action.transaction.max_fee_per_gas,
        "logs": [{
            "address": action.token_contract,
            "topics": [
                APPROVAL_EVENT_TOPIC,
                "0x" + action.sender[2:].lower().rjust(64, "0"),
                "0x" + action.spender[2:].lower().rjust(64, "0"),
            ],
            "data": "0x" + bytes(32).hex(),
        }],
    }
    record = history.load()[0]
    malformed = dict(rpc.receipt)
    malformed["logs"] = [{**rpc.receipt["logs"][0], "data": "0x" + (1).to_bytes(32, "big").hex()}]
    assert _receipt_status(malformed, record, rpc.public_transaction) is HistoryStatus.UNKNOWN
    reverted = dict(rpc.receipt)
    reverted["status"] = 0
    assert _receipt_status(reverted, record, rpc.public_transaction) is HistoryStatus.FAILED
    tracked = BroadcastReceiptTracker(
        history, lambda _endpoint: rpc, environ, timeout_seconds=0,
    ).check_once(action.action_id)
    assert tracked.status is HistoryStatus.CONFIRMED


def test_changed_allowance_fails_before_authentication(tmp_path, monkeypatch) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = secrets.token_urlsafe(18)
    summary = profile(repository, password)
    environ = policy_env()
    policy = RevokePolicy.from_environment(environ)
    rpc = ApprovalRpcStub()
    action = RevokePreflightService(
        policy, lambda _endpoint: rpc, environ,
    ).prepare(RevokeFlowCoordinator().begin(summary.profile_id, "base"), summary)
    history = HistoryStore(repository.paths)
    history.append(_history_record(action))
    rpc.allowance_value += 1
    monkeypatch.setattr(
        repository,
        "_authenticate_profile",
        lambda *_args: pytest.fail("authentication must not run"),
    )
    result = MainnetTransferExecutor(
        repository,
        history,
        MainnetBroadcastPolicy(False, OfflineSigningPolicy(None)),
        lambda _endpoint: rpc,
        environ,
        revoke_policy=policy,
    ).execute(action, action.digest, password, SigningPermit())
    assert result.code is MainnetTransferCode.REVALIDATION_FAILED
    assert rpc.send_calls == 0
