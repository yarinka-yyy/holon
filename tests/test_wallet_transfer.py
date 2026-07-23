from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from requests import exceptions as request_errors
from web3 import Web3

from holon_wallet.model import ProfileSummary
from holon_wallet.public_data import BASE_USDC, ETHEREUM_USDC
from holon_wallet.transfer import (
    BASE_CHAIN_ID,
    ETH_ASSET_ID,
    PendingTransferRequest,
    TRANSFER_ROUTES,
    TransferFlowCoordinator,
    TransferFlowError,
    TransferFlowState,
    TransferPreflightCode,
    TransferPreflightError,
    TransferPreflightService,
    USDC_AMOUNT_ATOMIC,
    encode_usdc_transfer,
    format_atomic_amount,
    normalize_recipient,
    parse_transfer_amount,
    transfer_route,
    transfer_action_to_map,
)

NOW = datetime(2026, 7, 21, 12, 0, tzinfo=UTC)
SENDER = Web3.to_checksum_address("0x" + "11" * 20)
RECIPIENT = Web3.to_checksum_address("0x" + "22" * 20)


def profile() -> ProfileSummary:
    return ProfileSummary(
        "profile-main",
        "Main Account",
        SENDER,
        "mnemonic",
        "m/44'/60'/0'/0/0",
        "2026-07-21T10:00:00Z",
    )


def request() -> PendingTransferRequest:
    return PendingTransferRequest(
        "act-fixed",
        profile().profile_id,
        NOW,
        NOW + timedelta(minutes=5),
    )


class StubTransferRpc:
    def __init__(self, expected_contract=BASE_USDC, **overrides) -> None:
        self.expected_contract = expected_contract
        self.values = {
            "chain_id": BASE_CHAIN_ID,
            "block": (123456, 10),
            "native": 10**18,
            "decimals": 6,
            "token": 2_000_000,
            "nonce": 7,
            "priority": 2,
            "gas": 50_000,
        }
        self.values.update(overrides)
        self.estimated_transaction = None

    def chain_id(self) -> int:
        return self._value("chain_id")

    def latest_block(self) -> tuple[int, int]:
        return self._value("block")

    def native_balance(self, _address: str) -> int:
        return self._value("native")

    def token_decimals(self, contract: str) -> int:
        assert contract == self.expected_contract
        return self._value("decimals")

    def token_balance(self, contract: str, _address: str) -> int:
        assert contract == self.expected_contract
        return self._value("token")

    def pending_nonce(self, _address: str) -> int:
        return self._value("nonce")

    def max_priority_fee_per_gas(self) -> int:
        return self._value("priority")

    def estimate_gas(self, transaction) -> int:
        self.estimated_transaction = dict(transaction)
        return self._value("gas")

    def _value(self, name):
        value = self.values[name]
        if isinstance(value, BaseException):
            raise value
        return value


def prepare(rpc: StubTransferRpc | None = None):
    item = rpc or StubTransferRpc()
    service = TransferPreflightService(lambda _endpoint: item, environ={})
    return service.prepare(request(), profile(), RECIPIENT), item


def test_recipient_normalization_and_guards() -> None:
    assert normalize_recipient(RECIPIENT.lower(), SENDER) == RECIPIENT
    with pytest.raises(TransferPreflightError) as invalid:
        normalize_recipient("0x1234", SENDER)
    assert invalid.value.code is TransferPreflightCode.INVALID_RECIPIENT

    bad_checksum = "0x52908400098527886e0F7030069857D2E4169EE7"
    with pytest.raises(TransferPreflightError) as checksum:
        normalize_recipient(bad_checksum, SENDER)
    assert checksum.value.code is TransferPreflightCode.INVALID_RECIPIENT

    for reserved in ("0x" + "00" * 20, SENDER, BASE_USDC, ETHEREUM_USDC):
        with pytest.raises(TransferPreflightError) as blocked:
            normalize_recipient(reserved, SENDER)
        assert blocked.value.code is TransferPreflightCode.RESERVED_RECIPIENT


@pytest.mark.parametrize(
    ("value", "decimals", "atomic", "canonical"),
    [
        ("1", 18, 10**18, "1"),
        ("0,000001", 6, 1, "0.000001"),
        ("001.2500", 6, 1_250_000, "1.25"),
        ("0.000000000000000001", 18, 1, "0.000000000000000001"),
    ],
)
def test_exact_amount_parser(value, decimals, atomic, canonical) -> None:
    assert parse_transfer_amount(value, decimals) == (atomic, canonical)
    assert format_atomic_amount(atomic, decimals) == canonical


def test_transfer_route_registry_is_immutable() -> None:
    with pytest.raises(TypeError):
        TRANSFER_ROUTES[("base", "eth")] = transfer_route("base", "eth")


@pytest.mark.parametrize(
    ("value", "decimals"),
    [
        ("0", 18), ("-1", 18), ("+1", 18), ("1e2", 18),
        ("1.", 18), (".1", 18), ("1,2.3", 18), ("0.0000001", 6),
        (" 1", 18), ("1 ", 18),
    ],
)
def test_amount_parser_rejects_noncanonical_or_inexact_input(value, decimals) -> None:
    with pytest.raises(TransferPreflightError) as failure:
        parse_transfer_amount(value, decimals)
    assert failure.value.code is TransferPreflightCode.INVALID_AMOUNT


@pytest.mark.parametrize(
    ("network_id", "asset_id", "amount_atomic"),
    [
        ("ethereum", "eth", 10**16),
        ("ethereum", "usdc", 2_500_001),
        ("base", "eth", 10**15),
        ("base", "usdc", 1_250_000),
    ],
)
def test_all_allowlisted_routes_build_exact_type2_transactions(
    network_id, asset_id, amount_atomic,
) -> None:
    route = transfer_route(network_id, asset_id)
    rpc = StubTransferRpc(
        expected_contract=route.token_contract,
        chain_id=route.chain_id,
        native=10**20,
        token=10_000_000,
    )
    pending = replace(
        request(), network_id=network_id, asset_id=asset_id,
        amount_atomic=amount_atomic,
    )
    action = TransferPreflightService(
        lambda _endpoint: rpc, environ={route.endpoint_env: "fixture://route"},
    ).prepare(pending, profile(), RECIPIENT)

    assert action.network_id == network_id
    assert action.asset_id == asset_id
    assert action.chain_id == route.chain_id
    assert action.amount_atomic == amount_atomic
    assert action.token_contract == route.token_contract
    if asset_id == ETH_ASSET_ID:
        assert action.transaction.to == RECIPIENT
        assert action.transaction.value == amount_atomic
        assert action.transaction.data == "0x"
    else:
        assert action.transaction.to == route.token_contract
        assert action.transaction.value == 0
        assert action.transaction.data == encode_usdc_transfer(
            RECIPIENT, amount_atomic,
        )


def test_native_maximum_quote_reserves_live_fee_envelope_with_headroom() -> None:
    route = transfer_route("ethereum", "eth")
    rpc = StubTransferRpc(
        expected_contract=None,
        chain_id=route.chain_id,
        native=10**18,
        gas=21_000,
    )
    service = TransferPreflightService(
        lambda _endpoint: rpc,
        environ={route.endpoint_env: "fixture://ethereum"},
    )

    maximum = service.quote_maximum_native(profile(), "ethereum", RECIPIENT)

    max_fee_per_gas = 2 * 10 + 2
    assert maximum == 10**18 - 23_100 * max_fee_per_gas
    assert rpc.estimated_transaction["to"] == RECIPIENT
    assert rpc.estimated_transaction["value"] < 10**18
    assert rpc.estimated_transaction["data"] == "0x"


def test_exact_calldata_unsigned_fields_fee_and_safe_map() -> None:
    action, rpc = prepare()
    expected_data = (
        "0xa9059cbb"
        + "0" * 24 + RECIPIENT[2:].lower()
        + f"{USDC_AMOUNT_ATOMIC:064x}"
    )

    assert encode_usdc_transfer(RECIPIENT, USDC_AMOUNT_ATOMIC) == expected_data
    assert action.transaction.data == expected_data
    assert action.transaction.transaction_type == 2
    assert action.transaction.chain_id == 8453
    assert action.transaction.to == BASE_USDC
    assert action.transaction.value == 0
    assert action.transaction.nonce == 7
    assert action.transaction.gas == 50_000
    assert action.transaction.max_priority_fee_per_gas == 2
    assert action.transaction.max_fee_per_gas == 22
    assert action.max_total_fee_wei == 1_100_000
    assert rpc.estimated_transaction["from"] == SENDER
    assert rpc.estimated_transaction["data"] == expected_data

    mapped = transfer_action_to_map(action)
    assert mapped["amountAtomic"] == "1000000"
    assert mapped["maxTotalFeeWei"] == "1100000"
    assert mapped["simulation"] is False
    assert "data" not in mapped
    assert expected_data not in repr(mapped)


def test_digest_is_deterministic_and_every_mutation_changes_it() -> None:
    first, _rpc = prepare()
    second, _rpc = prepare()
    assert first.digest == second.digest

    mutations = (
        replace(first, recipient=Web3.to_checksum_address("0x" + "33" * 20)),
        replace(first, max_total_fee_wei=first.max_total_fee_wei + 1),
        replace(first, transaction=replace(first.transaction, nonce=8)),
        replace(first, expires_at=first.expires_at + timedelta(seconds=1)),
    )
    assert all(item.digest != first.digest for item in mutations)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"chain_id": 1}, TransferPreflightCode.WRONG_CHAIN),
        ({"decimals": 18}, TransferPreflightCode.TOKEN_METADATA_INVALID),
        ({"token": 999_999}, TransferPreflightCode.INSUFFICIENT_USDC),
        ({"native": 1}, TransferPreflightCode.INSUFFICIENT_ETH),
        ({"gas": 0}, TransferPreflightCode.DATA_INVALID),
        ({"priority": -1}, TransferPreflightCode.DATA_INVALID),
    ],
)
def test_preflight_rejects_unsafe_or_invalid_public_data(overrides, expected) -> None:
    with pytest.raises(TransferPreflightError) as failure:
        prepare(StubTransferRpc(**overrides))
    assert failure.value.code is expected


def test_native_preflight_requires_amount_plus_maximum_fee() -> None:
    route = transfer_route("base", "eth")
    amount = 10**15
    pending = replace(
        request(), network_id="base", asset_id="eth", amount_atomic=amount,
    )
    # gas=50_000 and maxFeePerGas=22 produce a 1_100_000 wei maximum fee.
    rpc = StubTransferRpc(
        expected_contract=None,
        native=amount + 1_100_000 - 1,
    )
    service = TransferPreflightService(
        lambda _endpoint: rpc, environ={route.endpoint_env: "fixture://base"},
    )
    with pytest.raises(TransferPreflightError) as failure:
        service.prepare(pending, profile(), RECIPIENT)
    assert failure.value.code is TransferPreflightCode.INSUFFICIENT_ETH


def test_transport_retries_once_without_exposing_endpoint() -> None:
    calls = 0

    def factory(_endpoint: str):
        nonlocal calls
        calls += 1
        if calls == 1:
            return StubTransferRpc(chain_id=request_errors.Timeout("endpoint-canary"))
        return StubTransferRpc()

    service = TransferPreflightService(factory, environ={})
    action = service.prepare(request(), profile(), RECIPIENT)
    assert calls == 2
    assert action.recipient == RECIPIENT

    failing = TransferPreflightService(
        lambda _endpoint: StubTransferRpc(
            chain_id=request_errors.Timeout("endpoint-canary"),
        ),
        environ={"HOLON_BASE_RPC_URL": "https://secret-rpc.invalid/token"},
    )
    with pytest.raises(TransferPreflightError) as error:
        failing.prepare(request(), profile(), RECIPIENT)
    assert error.value.code is TransferPreflightCode.RPC_UNAVAILABLE
    assert "secret-rpc" not in str(error.value)
    assert "endpoint-canary" not in str(error.value)


def test_flow_is_single_active_expiring_and_replay_safe() -> None:
    clock_value = [NOW]
    ids = iter(("act-one", "act-one", "act-two"))
    flow = TransferFlowCoordinator(lambda: clock_value[0], lambda: next(ids))

    pending = flow.begin(profile().profile_id)
    assert flow.state is TransferFlowState.PREPARING
    with pytest.raises(TransferFlowError):
        flow.begin(profile().profile_id)

    action, _rpc = prepare()
    action = replace(
        action,
        action_id=pending.action_id,
        created_at=pending.created_at,
        expires_at=pending.expires_at,
    )
    assert flow.accept(action)
    assert flow.state is TransferFlowState.PREPARED
    assert flow.validate(action.action_id, action.digest, action.profile_id)
    flow._current = replace(action, recipient=Web3.to_checksum_address("0x" + "33" * 20))
    assert not flow.validate(action.action_id, action.digest, action.profile_id)
    assert flow.state is TransferFlowState.LOCKED

    with pytest.raises(TransferFlowError):
        flow.begin(profile().profile_id)
    pending = flow.begin(profile().profile_id)
    clock_value[0] = NOW + timedelta(minutes=5)
    expired = replace(
        action,
        action_id=pending.action_id,
        created_at=pending.created_at,
        expires_at=pending.expires_at,
    )
    assert not flow.accept(expired)
    assert flow.state is TransferFlowState.LOCKED


def test_profile_change_and_close_terminalize_pending_flow() -> None:
    flow = TransferFlowCoordinator(lambda: NOW, lambda: "act-profile")
    pending = flow.begin(profile().profile_id)
    assert flow.still_pending(pending.action_id, profile().profile_id)
    assert flow.profile_changed("different-profile")
    assert flow.state is TransferFlowState.LOCKED
    assert not flow.still_pending(pending.action_id, profile().profile_id)

    other = TransferFlowCoordinator(lambda: NOW, lambda: "act-close")
    other.begin(profile().profile_id)
    other.close()
    assert other.state is TransferFlowState.LOCKED


def test_external_flow_binds_guard_action_and_exact_expiry() -> None:
    flow = TransferFlowCoordinator(lambda: NOW)
    action_id = "act-22222222-2222-4222-8222-222222222222"
    pending = flow.begin_external(
        action_id,
        profile().profile_id,
        NOW,
        NOW + timedelta(minutes=5),
        "ethereum",
        "eth",
        10**15,
    )
    assert pending.action_id == action_id
    assert pending.network_id == "ethereum"
    assert pending.asset_id == "eth"
    flow.close()
    with pytest.raises(TransferFlowError):
        flow.begin_external(
            action_id,
            profile().profile_id,
            NOW,
            NOW + timedelta(minutes=5),
            "ethereum",
            "eth",
            10**15,
        )
    with pytest.raises(TransferFlowError):
        TransferFlowCoordinator(lambda: NOW).begin_external(
            "act-33333333-3333-4333-8333-333333333333",
            profile().profile_id,
            NOW,
            NOW + timedelta(minutes=4),
            "base",
            "usdc",
            1,
        )
