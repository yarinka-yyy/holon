from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from holon_contracts import ContractViolation, MessageKind, make_envelope
from holon_lending import (
    ACTION_PROFILES_DIGEST,
    ActionProfilesLoadError,
    ActionProfilesState,
    LendingPreflightCode,
    LendingPreflightError,
    LendingPreflightService,
    action_profiles_digest,
    canonical_action_profiles_bytes,
    encode_approve,
    encode_supply,
    encode_withdraw,
    load_action_profile,
)
from holon_lending.action_profiles import ACTION_PROFILES_PATH
from holon_lending.preflight import MAX_UINT256, unavailable_preview

NOW = datetime(2026, 7, 25, 18, 0, tzinfo=UTC)
BLOCK = 50_000_000
SENDER = "0x1111111111111111111111111111111111111111"


def intent(action: str = "supply", mode: str = "exact", amount: str | None = "1.25"):
    return {
        "module_id": "lending", "module_version": "1",
        "protocol_profile_id": "aave-v3-base-usdc",
        "protocol_profile_version": "1", "network": "base", "asset": "usdc",
        "beneficiary_mode": "active_wallet_account", "action": action,
        "amount_mode": mode, "amount": amount,
    }


class FakeRpc:
    def __init__(self, profile) -> None:
        self.profile = profile
        self.calls: list[str] = []
        self.block_time = int(NOW.timestamp()) - 10
        self.chain_ok = True
        self.active = True
        self.frozen = False
        self.paused = False
        self.debt = 0
        self.allowance_value = 0
        self.usdc_balance = 10_000_000
        self.position = 5_000_000
        self.liquidity = 100_000_000
        self.supply_cap = 1_000_000_000
        self.total_supply = 100_000_000
        self.native = 10**18
        self.simulation_error = False

    def begin(self):
        self.calls.append("begin")
        if not self.chain_ok:
            raise LendingPreflightError(LendingPreflightCode.WRONG_CHAIN)
        return BLOCK, self.block_time, 1_000_000_000

    def has_code(self, address, block):
        del block
        self.calls.append("has_code")
        return bool(address)

    def resolve_pool(self, provider, block):
        del provider, block
        return self.profile.pool

    def token_decimals(self, token, block):
        del token, block
        return 6

    def reserve_a_token(self, data_provider, asset, block):
        del data_provider, asset, block
        return self.profile.a_token

    def reserve_configuration(self, data_provider, asset, block):
        del data_provider, asset, block
        return 6, self.active, self.frozen

    def reserve_caps(self, data_provider, asset, block):
        del data_provider, asset, block
        return 0, self.supply_cap

    def reserve_paused(self, data_provider, asset, block):
        del data_provider, asset, block
        return self.paused

    def reserve_total_supply(self, data_provider, asset, block):
        del data_provider, asset, block
        return self.total_supply

    def account_debt(self, pool, account, block):
        del pool, account, block
        return self.debt

    def token_balance(self, token, account, block):
        del block
        if token == self.profile.a_token and account == SENDER:
            return self.position
        if token == self.profile.asset and account == self.profile.a_token:
            return self.liquidity
        return self.usdc_balance

    def allowance(self, token, owner, spender, block):
        del token, owner, spender, block
        return self.allowance_value

    def pending_nonce(self, account):
        del account
        return 7

    def native_balance(self, account, block):
        del account, block
        return self.native

    def priority_fee(self):
        return 100_000_000

    def estimate_gas(self, transaction):
        self.last_transaction = dict(transaction)
        return 75_000

    def l1_fee_upper_bound(self, transaction_size, block):
        assert transaction_size == 512
        del block
        return 2_000_000_000_000

    def simulate(self, transaction):
        self.calls.append("simulate")
        if self.simulation_error:
            raise RuntimeError("revert detail")
        assert transaction["from"] == SENDER
        return b""


@pytest.fixture
def setup_service():
    state = ActionProfilesState.load()
    assert state.profile is not None
    rpc = FakeRpc(state.profile)
    service = LendingPreflightService(
        state, lambda: rpc, clock=lambda: NOW,
    )
    return state.profile, rpc, service


def test_action_profile_is_canonical_integrity_pinned_and_exact(tmp_path: Path) -> None:
    raw = json.loads(ACTION_PROFILES_PATH.read_text(encoding="utf-8"))
    assert ACTION_PROFILES_PATH.read_bytes() == canonical_action_profiles_bytes(raw)
    assert action_profiles_digest(raw) == ACTION_PROFILES_DIGEST
    profile = load_action_profile()
    assert profile.profile_id == "aave-v3-base-usdc"
    assert profile.digest == ACTION_PROFILES_DIGEST
    assert profile.chain_id == 8453
    for mutation, code in (
        (lambda value: value.update({"extra": True}), "ACTION_PROFILES_INTEGRITY_FAILED"),
        (lambda value: value["protocol"].update({"pool": SENDER}), "ACTION_PROFILES_INTEGRITY_FAILED"),
        (lambda value: value.update({"schema_version": "2"}), "ACTION_PROFILES_INCOMPATIBLE"),
    ):
        changed = copy.deepcopy(raw)
        mutation(changed)
        path = tmp_path / f"{len(list(tmp_path.iterdir()))}.json"
        path.write_bytes(canonical_action_profiles_bytes(changed))
        with pytest.raises(ActionProfilesLoadError) as raised:
            load_action_profile(path)
        assert raised.value.code == code
    with pytest.raises(ActionProfilesLoadError) as missing:
        load_action_profile(tmp_path / "missing.json")
    assert missing.value.code == "ACTION_PROFILES_MISSING"
    corrupt = tmp_path / "corrupt.json"
    corrupt.write_text("{not-json", encoding="utf-8")
    with pytest.raises(ActionProfilesLoadError) as malformed:
        load_action_profile(corrupt)
    assert malformed.value.code == "ACTION_PROFILES_CORRUPT"


def test_exact_encoders_pin_only_approved_methods(setup_service) -> None:
    profile, _rpc, _service = setup_service
    approve = encode_approve(profile.pool, 1_250_000)
    supply = encode_supply(profile.asset, 1_250_000, SENDER)
    withdraw = encode_withdraw(profile.asset, MAX_UINT256, SENDER)
    assert approve.startswith("0x095ea7b3") and len(approve) == 2 + 8 + 64 * 2
    assert supply.startswith("0x617ba037") and len(supply) == 2 + 8 + 64 * 4
    assert withdraw.startswith("0x69328dec") and len(withdraw) == 2 + 8 + 64 * 3


def test_supply_with_zero_allowance_returns_exact_approval_preview(setup_service) -> None:
    profile, rpc, service = setup_service
    result = service.prepare(
        intent(), {"label": "Main", "address": SENDER},
        expected_profile_digest=profile.digest,
    )
    make_envelope(MessageKind.LENDING_ACTION_PREVIEW, result)
    assert result["status"] == "PREVIEW_READY"
    assert result["requested_action"] == "supply"
    assert result["next_action"] == "approve"
    assert result["target"] == profile.asset
    assert result["amount_atomic"] == "1250000"
    assert result["display_amount"] == "1.25 USDC"
    assert result["authority_available"] is False
    assert result["execution_available"] is False
    assert "data" not in result and "calldata" not in result
    assert rpc.last_transaction["data"].startswith("0x095ea7b3")


def test_exact_allowance_returns_supply_and_material_digest_changes(setup_service) -> None:
    profile, rpc, service = setup_service
    rpc.allowance_value = 1_250_000
    first = service.prepare(
        intent(), {"label": "Main", "address": SENDER},
        expected_profile_digest=profile.digest,
    )
    assert first["next_action"] == "supply"
    assert first["target"] == profile.pool
    assert rpc.last_transaction["data"].startswith("0x617ba037")
    rpc.allowance_value = 2_000_000
    second = service.prepare(
        intent(amount="2"), {"label": "Main", "address": SENDER},
        expected_profile_digest=profile.digest,
    )
    assert first["preview_digest"] != second["preview_digest"]


def test_exact_and_all_withdraw_are_distinct_and_use_live_position(setup_service) -> None:
    profile, rpc, service = setup_service
    exact = service.prepare(
        intent("withdraw", "exact", "2"), {"label": "Main", "address": SENDER},
        expected_profile_digest=profile.digest,
    )
    all_result = service.prepare(
        intent("withdraw", "all", None), {"label": "Main", "address": SENDER},
        expected_profile_digest=profile.digest,
    )
    assert exact["amount_atomic"] == "2000000"
    assert all_result["amount_atomic"] == str(rpc.position)
    assert all_result["display_amount"] == "5 USDC"
    assert exact["preview_digest"] != all_result["preview_digest"]
    assert rpc.last_transaction["data"] == encode_withdraw(profile.asset, MAX_UINT256, SENDER)
    assert datetime.fromisoformat(
        all_result["expires_at"].replace("Z", "+00:00"),
    ) == NOW.replace(microsecond=0) + timedelta(minutes=5)
    assert len(json.dumps(all_result, separators=(",", ":")).encode()) < 8 * 1024


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda rpc: setattr(rpc, "active", False), "AAVE_RESERVE_INACTIVE"),
        (lambda rpc: setattr(rpc, "frozen", True), "AAVE_RESERVE_FROZEN"),
        (lambda rpc: setattr(rpc, "paused", True), "AAVE_RESERVE_PAUSED"),
        (lambda rpc: setattr(rpc, "debt", 1), "AAVE_ACCOUNT_HAS_DEBT"),
        (lambda rpc: setattr(rpc, "usdc_balance", 1), "INSUFFICIENT_USDC"),
        (lambda rpc: setattr(rpc, "allowance_value", 1), "UNEXPECTED_ALLOWANCE"),
        (lambda rpc: setattr(rpc, "native", 1), "INSUFFICIENT_ETH"),
        (lambda rpc: setattr(rpc, "supply_cap", 1), "AAVE_SUPPLY_CAP_REACHED"),
        (lambda rpc: setattr(rpc, "estimate_gas", lambda transaction: 0), "GAS_ESTIMATE_FAILED"),
        (lambda rpc: setattr(rpc, "simulation_error", True), "SIMULATION_FAILED"),
    ],
)
def test_supply_failures_are_exact(setup_service, mutate, code) -> None:
    profile, rpc, service = setup_service
    mutate(rpc)
    with pytest.raises(LendingPreflightError) as raised:
        service.prepare(
            intent(), {"label": "Main", "address": SENDER},
            expected_profile_digest=profile.digest,
        )
    assert raised.value.code == code


@pytest.mark.parametrize(
    ("mode", "amount", "position", "liquidity", "code"),
    [
        ("exact", "6", 5_000_000, 100_000_000, "INSUFFICIENT_AUSDC"),
        ("all", None, 0, 100_000_000, "INSUFFICIENT_AUSDC"),
        ("exact", "2", 5_000_000, 1_000_000, "INSUFFICIENT_PROTOCOL_LIQUIDITY"),
    ],
)
def test_withdraw_refuses_missing_position_or_liquidity(
    setup_service, mode, amount, position, liquidity, code,
) -> None:
    profile, rpc, service = setup_service
    rpc.position = position
    rpc.liquidity = liquidity
    with pytest.raises(LendingPreflightError) as raised:
        service.prepare(
            intent("withdraw", mode, amount), {"label": "Main", "address": SENDER},
            expected_profile_digest=profile.digest,
        )
    assert raised.value.code == code


@pytest.mark.parametrize("offset", [-121, 61])
def test_old_or_future_block_is_unavailable(setup_service, offset: int) -> None:
    profile, rpc, service = setup_service
    rpc.block_time = int(NOW.timestamp()) + offset
    with pytest.raises(LendingPreflightError) as raised:
        service.prepare(
            intent(), {"label": "Main", "address": SENDER},
            expected_profile_digest=profile.digest,
        )
    assert raised.value.code == "AAVE_BLOCK_STALE"


def test_wrong_chain_or_contract_identity_is_unavailable(setup_service) -> None:
    profile, rpc, service = setup_service
    rpc.chain_ok = False
    with pytest.raises(LendingPreflightError) as wrong_chain:
        service.prepare(
            intent(), {"label": "Main", "address": SENDER},
            expected_profile_digest=profile.digest,
        )
    assert wrong_chain.value.code == "WRONG_CHAIN"
    rpc.chain_ok = True
    rpc.resolve_pool = lambda provider, block: SENDER
    with pytest.raises(LendingPreflightError) as identity:
        service.prepare(
            intent(), {"label": "Main", "address": SENDER},
            expected_profile_digest=profile.digest,
        )
    assert identity.value.code == "AAVE_IDENTITY_MISMATCH"


def test_profile_failure_and_digest_mismatch_make_no_rpc_call(setup_service) -> None:
    profile, rpc, service = setup_service
    with pytest.raises(LendingPreflightError) as raised:
        service.prepare(
            intent(), {"label": "Main", "address": SENDER},
            expected_profile_digest="0" * 64,
        )
    assert raised.value.code == "ACTION_PROFILE_MISMATCH"
    assert rpc.calls == []
    unavailable = LendingPreflightService(
        ActionProfilesState("UNAVAILABLE", None, "ACTION_PROFILES_CORRUPT"),
        lambda: (_ for _ in ()).throw(AssertionError("RPC")),
    )
    with pytest.raises(LendingPreflightError) as failed:
        unavailable.prepare(
            intent(), {"label": "Main", "address": SENDER},
            expected_profile_digest=profile.digest,
        )
    assert failed.value.code == "ACTION_PROFILES_CORRUPT"


@pytest.mark.parametrize(
    "bad",
    [
        intent("withdraw", "all", "1"),
        intent(amount="0"), intent(amount="1.0000001"), intent(amount="1e3"),
    ],
)
def test_semantic_contract_rejects_invalid_amount_combinations(bad) -> None:
    with pytest.raises((ContractViolation, LendingPreflightError)):
        make_envelope(MessageKind.LENDING_ACTION_INTENT, bad)


def test_semantic_contract_accepts_supply_all_without_amount() -> None:
    message = make_envelope(
        MessageKind.LENDING_ACTION_INTENT, intent("supply", "all", None),
    )
    assert message.payload["amount"] is None


def test_unavailable_preview_is_strict_and_contains_no_action_material() -> None:
    value = unavailable_preview("BASE_RPC_UNAVAILABLE", requested_action="supply", amount_mode="exact")
    message = make_envelope(MessageKind.LENDING_ACTION_PREVIEW, value)
    assert message.payload["status"] == "UNAVAILABLE"
    assert message.payload["target"] is None
    assert message.payload["checks"] == []
