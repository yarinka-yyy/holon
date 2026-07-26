from __future__ import annotations

from datetime import UTC, datetime, timedelta
from dataclasses import replace

import pytest

from holon_lending import (
    ACTION_PROFILES_DIGEST, ActionProfilesState, LendingPreflightService,
    encode_approve, encode_supply, encode_withdraw,
)
from holon_lending.preflight import MAX_UINT256
from holon_policy import LendingRule, Policy
from holon_wallet.broadcast import (
    BASE_RPC_ENV, BroadcastReceiptTracker, MainnetBroadcastPolicy,
    MainnetTransferCode, MainnetTransferExecutor,
)
from holon_wallet.history import HistoryStatus, HistoryStore, WalletHistoryRecord
from holon_wallet.lending_action import prepare_lending_action
from holon_wallet.model import ProfileSummary
from holon_wallet.signer import validate_signing_action
from holon_wallet.storage import WalletPaths
from holon_wallet.transfer import SigningPermit
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic
from web3 import Web3


SENDER = "0x1111111111111111111111111111111111111111"


class Rpc:
    def __init__(self, profile, allowance=0, position=999_999):
        self.profile = profile
        self.allowance_value = allowance
        self.position = position

    def begin(self): return 10_000, int(datetime.now(UTC).timestamp()), 10
    def has_code(self, address, block): del address, block; return True
    def resolve_pool(self, provider, block): del provider, block; return self.profile.pool
    def token_decimals(self, token, block): del token, block; return 6
    def reserve_a_token(self, provider, asset, block): del provider, asset, block; return self.profile.a_token
    def reserve_configuration(self, provider, asset, block): del provider, asset, block; return 6, True, False
    def reserve_paused(self, provider, asset, block): del provider, asset, block; return False
    def reserve_caps(self, provider, asset, block): del provider, asset, block; return 0, 0
    def reserve_total_supply(self, provider, asset, block): del provider, asset, block; return 0
    def account_debt(self, pool, account, block): del pool, account, block; return 0
    def token_balance(self, token, account, block):
        del block
        if token == self.profile.a_token and account.lower() != self.profile.a_token.lower():
            return self.position
        return 10_000_000
    def allowance(self, token, owner, spender, block): del token, owner, spender, block; return self.allowance_value
    def pending_nonce(self, account): del account; return 7
    def native_balance(self, account, block): del account, block; return 10**18
    def priority_fee(self): return 2
    def estimate_gas(self, transaction): del transaction; return 80_000
    def l1_fee_upper_bound(self, size, block): assert size == 512; del block; return 20_000
    def simulate(self, transaction): del transaction; return b""


def request(
    now: datetime, action: str = "supply", amount_mode: str = "exact",
    amount: str | None = "1",
) -> dict[str, object]:
    return {
        "kind": "prepare_lending_action", "action_id": "act-lending-one",
        "action": action, "amount_mode": amount_mode, "amount": amount,
        "policy_revision": 2, "policy_digest": "c" * 64,
        "action_profile_digest": ACTION_PROFILES_DIGEST,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }


class ExecutionRpc:
    def __init__(self, profile, action, exact_l1_fee):
        self.profile, self.action, self.exact_l1_fee = profile, action, exact_l1_fee
        self.send_calls = 0
    def chain_id(self): return 8453
    def latest_block(self): return self.action.block_number, 10
    def native_balance(self, address): del address; return 10**18
    def token_decimals(self, token): del token; return 6
    def token_balance(self, token, address): del token, address; return 10_000_000
    def allowance(self, token, owner, spender): del token, owner, spender; return 0
    def pending_nonce(self, address): del address; return self.action.transaction.nonce
    def max_priority_fee_per_gas(self): return self.action.transaction.max_priority_fee_per_gas
    def estimate_gas(self, transaction): del transaction; return self.action.transaction.gas
    def l1_fee(self, raw): assert isinstance(raw, bytes) and raw; return self.exact_l1_fee
    def lending_has_code(self, address, block): del address, block; return True
    def lending_resolve_pool(self, provider, block): del provider, block; return self.profile.pool
    def lending_token_decimals(self, token, block): del token, block; return 6
    def lending_reserve_a_token(self, provider, asset, block): del provider, asset, block; return self.profile.a_token
    def lending_reserve_configuration(self, provider, asset, block): del provider, asset, block; return 6, True, False
    def lending_reserve_caps(self, provider, asset, block): del provider, asset, block; return 0, 0
    def lending_reserve_paused(self, provider, asset, block): del provider, asset, block; return False
    def lending_reserve_total_supply(self, provider, asset, block): del provider, asset, block; return 0
    def lending_account_debt(self, pool, account, block): del pool, account, block; return 0
    def lending_token_balance(self, token, account, block):
        del block
        if token == self.profile.a_token and account.lower() == self.action.sender.lower():
            return self.action.amount_atomic
        return 10_000_000
    def lending_allowance(self, token, owner, spender, block): del token, owner, spender, block; return 0
    def lending_simulate(self, transaction): del transaction; return b""
    def send_raw_transaction(self, raw): self.send_calls += 1; return Web3.to_hex(Web3.keccak(raw))
    def transaction(self, tx_hash): del tx_hash; return None
    def transaction_receipt(self, tx_hash): del tx_hash; return None


def test_fresh_preflight_creates_independent_approve_then_supply_actions() -> None:
    state = ActionProfilesState.load()
    assert state.profile is not None
    account = ProfileSummary(
        "profile-one", "Main", SENDER, "mnemonic", "m/44'/60'/0'/0/0",
        "2026-07-26T00:00:00Z",
    )
    now = datetime.now(UTC).replace(microsecond=0)
    first_rpc = Rpc(state.profile, 0)
    first = prepare_lending_action(
        LendingPreflightService(state, lambda: first_rpc), state, account, request(now),
    )
    assert first.action_type == "lending" and first.method == "approve"
    assert first.action_profile_digest == ACTION_PROFILES_DIGEST
    assert first.max_total_fee_wei == first.l2_fee_ceiling_wei + first.l1_fee_upper_bound_wei
    assert validate_signing_action(first, first.digest, now) is None

    second_request = request(now)
    second_request["action_id"] = "act-lending-two"
    second = prepare_lending_action(
        LendingPreflightService(state, lambda: Rpc(state.profile, 1_000_000)),
        state, account, second_request,
    )
    assert second.method == "supply"
    assert second.action_id != first.action_id and second.digest != first.digest


def test_exact_and_all_withdraw_bind_distinct_calldata_and_mode() -> None:
    state = ActionProfilesState.load()
    assert state.profile is not None
    account = ProfileSummary(
        "profile-one", "Main", SENDER, "mnemonic", "m", "2026-07-26T00:00:00Z",
    )
    now = datetime.now(UTC).replace(microsecond=0)
    exact = prepare_lending_action(
        LendingPreflightService(state, lambda: Rpc(state.profile)), state, account,
        request(now, "withdraw", "exact", "0.5"),
    )
    all_request = request(now, "withdraw", "all", None)
    all_request["action_id"] = "act-lending-all"
    all_action = prepare_lending_action(
        LendingPreflightService(state, lambda: Rpc(state.profile)), state, account,
        all_request,
    )

    assert exact.method == "withdraw" and exact.amount_mode == "exact"
    assert exact.amount_atomic == 500_000
    assert exact.transaction.data == encode_withdraw(state.profile.asset, 500_000, SENDER)
    assert all_action.method == "withdraw" and all_action.amount_mode == "all"
    assert all_action.amount_atomic == 999_999
    assert all_action.transaction.data == encode_withdraw(
        state.profile.asset, MAX_UINT256, SENDER,
    )
    assert exact.digest != all_action.digest
    assert validate_signing_action(exact, exact.digest, now) is None
    assert validate_signing_action(all_action, all_action.digest, now) is None


def test_withdraw_all_policy_caps_resolved_position_not_sentinel() -> None:
    rule = LendingRule(
        "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
        ("withdraw",), "1010000", "100000000000000", ACTION_PROFILES_DIGEST,
    )
    policy = MainnetBroadcastPolicy.from_policy(
        Policy("3", "2", False, (), True, (rule,)),
    )
    state = ActionProfilesState.load()
    assert state.profile is not None
    now = datetime.now(UTC).replace(microsecond=0)
    account = ProfileSummary("p", "Main", SENDER, "mnemonic", "m", "2026-07-26T00:00:00Z")
    allowed = prepare_lending_action(
        LendingPreflightService(state, lambda: Rpc(state.profile, position=999_999)),
        state, account, request(now, "withdraw", "all", None),
    )
    allowed = replace(
        allowed, policy_revision=policy.policy_revision,
        policy_digest=policy.policy_digest_value,
    )
    assert policy.evaluate(allowed) is None

    over_request = request(now, "withdraw", "all", None)
    over_request["action_id"] = "act-lending-over"
    over = prepare_lending_action(
        LendingPreflightService(state, lambda: Rpc(state.profile, position=1_010_001)),
        state, account, over_request,
    )
    over = replace(
        over, policy_revision=policy.policy_revision,
        policy_digest=policy.policy_digest_value,
    )
    assert policy.evaluate(over) is MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED


def test_lending_policy_caps_fee_and_never_enables_send() -> None:
    rule = LendingRule(
        "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
        ("approve", "supply"), "5000000", "100000000000000",
        ACTION_PROFILES_DIGEST,
    )
    policy = MainnetBroadcastPolicy.from_policy(
        Policy("3", "2", False, (), True, (rule,)),
    )
    state = ActionProfilesState.load()
    assert state.profile is not None
    now = datetime.now(UTC).replace(microsecond=0)
    action = prepare_lending_action(
        LendingPreflightService(state, lambda: Rpc(state.profile)), state,
        ProfileSummary("p", "Main", SENDER, "mnemonic", "m", "2026-07-26T00:00:00Z"),
        request(now),
    )
    action = replace(
        action, policy_revision=policy.policy_revision,
        policy_digest=policy.policy_digest_value,
    )
    assert policy.evaluate(action) is None
    assert not policy.available


def test_exact_signed_l1_fee_is_rechecked_before_single_broadcast(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = "correct horse battery staple"
    record = repository.new_record(generate_mnemonic(), "Main")
    repository.create_new(password, record)
    state = ActionProfilesState.load()
    assert state.profile is not None
    rule = LendingRule(
        "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
        ("approve", "supply"), "5000000", "100000000000000",
        ACTION_PROFILES_DIGEST,
    )
    policy = MainnetBroadcastPolicy.from_policy(
        Policy("3", "2", False, (), True, (rule,)),
    )
    now = datetime.now(UTC).replace(microsecond=0)
    raw_request = request(now)
    raw_request.update({
        "policy_revision": policy.policy_revision,
        "policy_digest": policy.policy_digest_value,
    })
    action = prepare_lending_action(
        LendingPreflightService(state, lambda: Rpc(state.profile)), state,
        record.summary, raw_request,
    )
    history = HistoryStore(repository.paths)
    stamp = now.isoformat().replace("+00:00", "Z")
    history.append(WalletHistoryRecord(
        action.action_id, action.profile_id, "lending_approve", "base", 8453,
        action.sender, action.recipient, action.transaction.to, "USDC",
        str(action.amount_atomic), 6, None, HistoryStatus.PREPARED, stamp, stamp,
        False, str(action.max_total_fee_wei),
    ))
    rpc = ExecutionRpc(state.profile, action, action.l1_fee_upper_bound_wei - 1)
    executor = MainnetTransferExecutor(
        repository, history, policy, lambda endpoint: rpc,
        {"HOLON_BASE_RPC_URL": "fixture://base"}, lambda: now,
    )
    result = executor.execute(action, action.digest, password, SigningPermit())
    assert result.code is MainnetTransferCode.PENDING
    assert rpc.send_calls == 1

    second = prepare_lending_action(
        LendingPreflightService(state, lambda: Rpc(state.profile)), state,
        record.summary, dict(raw_request, action_id="act-lending-excess"),
    )
    history.append(WalletHistoryRecord(
        second.action_id, second.profile_id, "lending_approve", "base", 8453,
        second.sender, second.recipient, second.transaction.to, "USDC",
        str(second.amount_atomic), 6, None, HistoryStatus.PREPARED, stamp, stamp,
        False, str(second.max_total_fee_wei),
    ))
    excess_rpc = ExecutionRpc(
        state.profile, second, second.l1_fee_upper_bound_wei + 1,
    )
    excess = MainnetTransferExecutor(
        repository, history, policy, lambda endpoint: excess_rpc,
        {"HOLON_BASE_RPC_URL": "fixture://base"}, lambda: now,
    ).execute(second, second.digest, password, SigningPermit())
    assert excess.code is MainnetTransferCode.FEE_LIMIT_EXCEEDED
    assert excess_rpc.send_calls == 0


def test_withdraw_all_revalidates_resolved_position_and_broadcasts_once(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    password = "correct horse battery staple"
    record = repository.new_record(generate_mnemonic(), "Main")
    repository.create_new(password, record)
    state = ActionProfilesState.load()
    assert state.profile is not None
    rule = LendingRule(
        "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc", 8453,
        ("withdraw",), "1010000", "100000000000000", ACTION_PROFILES_DIGEST,
    )
    policy = MainnetBroadcastPolicy.from_policy(
        Policy("3", "2", False, (), True, (rule,)),
    )
    now = datetime.now(UTC).replace(microsecond=0)
    raw_request = request(now, "withdraw", "all", None)
    raw_request.update({
        "policy_revision": policy.policy_revision,
        "policy_digest": policy.policy_digest_value,
    })
    action = prepare_lending_action(
        LendingPreflightService(
            state, lambda: Rpc(state.profile, position=999_999),
        ),
        state, record.summary, raw_request,
    )
    stamp = now.isoformat().replace("+00:00", "Z")
    history = HistoryStore(repository.paths)
    history.append(WalletHistoryRecord(
        action.action_id, action.profile_id, "lending_withdraw_all", "base", 8453,
        action.sender, action.recipient, action.transaction.to, "USDC",
        str(action.amount_atomic), 6, None, HistoryStatus.PREPARED, stamp, stamp,
        False, str(action.max_total_fee_wei),
    ))
    rpc = ExecutionRpc(
        state.profile, action, action.l1_fee_upper_bound_wei - 1,
    )
    result = MainnetTransferExecutor(
        repository, history, policy, lambda endpoint: rpc,
        {"HOLON_BASE_RPC_URL": "fixture://base"}, lambda: now,
    ).execute(action, action.digest, password, SigningPermit())
    assert result.code is MainnetTransferCode.PENDING
    assert rpc.send_calls == 1


@pytest.mark.parametrize("action_type", [
    "lending_approve", "lending_supply", "lending_withdraw",
    "lending_withdraw_all",
])
def test_lending_receipt_tracker_fetches_transaction_and_confirms(
    tmp_path, action_type,
) -> None:
    state = ActionProfilesState.load()
    assert state.profile is not None
    profile = state.profile
    suffix = {
        "lending_approve": "11", "lending_supply": "22",
        "lending_withdraw": "33", "lending_withdraw_all": "44",
    }[action_type]
    transaction_hash = "0x" + suffix * 32
    target = profile.asset if action_type.endswith("approve") else profile.pool
    if action_type.endswith("approve"):
        calldata = encode_approve(profile.pool, 1_000_000)
    elif action_type.endswith("supply"):
        calldata = encode_supply(profile.asset, 1_000_000, SENDER)
    else:
        calldata = encode_withdraw(
            profile.asset,
            MAX_UINT256 if action_type.endswith("_all") else 1_000_000,
            SENDER,
        )
    history = HistoryStore(WalletPaths(tmp_path / action_type))
    history.append(WalletHistoryRecord(
        "act-" + action_type, "profile", action_type, "base", 8453,
        SENDER, profile.pool, target, "USDC", "1000000", 6,
        transaction_hash, HistoryStatus.UNKNOWN, "2026-07-26T00:00:00Z",
        "2026-07-26T00:00:00Z", False, "100000000000000",
    ))

    class ReceiptRpc:
        transaction_calls = 0

        def chain_id(self):
            return 8453

        def transaction_receipt(self, _transaction_hash):
            return {
                "transactionHash": transaction_hash, "from": SENDER, "to": target,
                "status": 1, "gasUsed": 100, "effectiveGasPrice": 2,
                "l1Fee": "0x3", "logs": [],
            }

        def transaction(self, _transaction_hash):
            self.transaction_calls += 1
            return {
                "hash": transaction_hash, "from": SENDER, "to": target,
                "value": 0, "input": calldata, "chainId": 8453,
            }

    rpc = ReceiptRpc()
    result = BroadcastReceiptTracker(
        history, lambda _endpoint: rpc, {BASE_RPC_ENV: "fixture://base"},
        timeout_seconds=0,
    ).check_once("act-" + action_type)

    assert result.status is HistoryStatus.CONFIRMED
    assert rpc.transaction_calls == 1
    assert history.load()[0].status is HistoryStatus.CONFIRMED
    assert history.load()[0].actual_fee_wei == "203"
