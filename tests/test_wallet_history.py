from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from holon_wallet.history import (
    HISTORY_SCHEMA_VERSION,
    MAX_HISTORY_RECORDS,
    HistoryStatus,
    HistoryStore,
    HistoryUnavailableError,
    HistoryValidationError,
    WalletHistoryRecord,
    history_record_to_map,
    lending_cashflows,
)
from holon_wallet.storage import StorageError, WalletPaths, atomic_write_json
from holon_wallet.public_data import NETWORK_BY_ID


SENDER = "0x" + "11" * 20
RECIPIENT = "0x" + "22" * 20
HASH = "0x" + "33" * 32
BRIDGE2 = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"


def record(index: int = 1, **changes) -> WalletHistoryRecord:
    value = WalletHistoryRecord(
        action_id=f"act-{index}",
        profile_id="profile-1",
        action_type="transfer",
        network="base",
        chain_id=8453,
        sender=SENDER,
        recipient=RECIPIENT,
        contract=None,
        token="USDC",
        amount_atomic="1000000",
        decimals=6,
        transaction_hash=None,
        status=HistoryStatus.PREPARED,
        created_at="2026-07-20T12:00:00Z",
        updated_at="2026-07-20T12:00:00Z",
        simulated=False,
    )
    return replace(value, **changes)


def funding_record(index: int = 1, **changes) -> WalletHistoryRecord:
    values = {
        "action_type": "perpdex_funding", "network": "arbitrum", "chain_id": 42161,
        "recipient": BRIDGE2, "contract": NETWORK_BY_ID["arbitrum"].usdc_contract,
        "operation_id": f"act-{index}",
    }
    values.update(changes)
    return record(index, **values)


def test_append_update_and_restart_are_atomic_public_history(tmp_path) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    assert store.load() == ()

    stored = store.append(record())
    assert stored == (record(),)
    updated = store.update_status(
        "act-1", HistoryStatus.PENDING, "2026-07-20T12:01:00Z", HASH,
    )
    assert updated[0].status is HistoryStatus.PENDING
    assert updated[0].transaction_hash == HASH

    restarted = HistoryStore(WalletPaths(tmp_path)).load()
    assert restarted == updated
    raw = store.path.read_text(encoding="utf-8")
    assert "password" not in raw.lower()
    assert "mnemonic" not in raw.lower()
    assert "private_key" not in raw.lower()


def test_v1_loads_without_fee_fields_and_migrates_on_next_mutation(tmp_path) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    legacy = record().to_dict()
    legacy.pop("max_total_fee_wei")
    legacy.pop("actual_fee_wei")
    legacy.pop("operation_id")
    legacy.pop("position_before_atomic")
    atomic_write_json(store.path, {"schema_version": 1, "records": [legacy]})

    loaded = store.load()
    assert loaded[0].max_total_fee_wei is None
    assert loaded[0].actual_fee_wei is None
    assert '"schema_version": 1' in store.path.read_text(encoding="utf-8")

    store.update_status(
        "act-1", HistoryStatus.PENDING, "2026-07-20T12:01:00Z", HASH,
    )
    migrated = store.path.read_text(encoding="utf-8")
    assert f'"schema_version": {HISTORY_SCHEMA_VERSION}' in migrated
    assert '"max_total_fee_wei": null' in migrated
    assert '"receipt_code": null' in migrated
    assert '"receipt_endpoint_class": null' in migrated


def test_v5_loads_and_migrates_to_v7_on_next_mutation(tmp_path) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    legacy = record(
        action_type="lending_approve",
        contract="0x" + "44" * 20,
        operation_id="operation-1",
        protocol_id="morpho-v1",
        call_amount_atomic="1000000",
        position_after_atomic="0",
        allowance_after_atomic="1000000",
        position_verified=True,
    )
    atomic_write_json(
        store.path,
        {"schema_version": 5, "records": [legacy.to_dict(include_v5=True)]},
    )

    loaded = store.load()
    assert loaded[0].protocol_id == "morpho-v1"
    assert loaded[0].receipt_code is None
    store.update_status(
        "act-1", HistoryStatus.UNKNOWN, "2026-07-20T12:01:00Z", HASH,
        receipt_code="RECEIPT_RPC_UNAVAILABLE",
        receipt_endpoint_class="official",
    )

    migrated = store.path.read_text(encoding="utf-8")
    assert f'"schema_version": {HISTORY_SCHEMA_VERSION}' in migrated
    assert '"receipt_code": "RECEIPT_RPC_UNAVAILABLE"' in migrated
    assert '"receipt_endpoint_class": "official"' in migrated


@pytest.mark.parametrize("schema_version", [2, 3, 4])
def test_v2_through_v4_history_remains_readable(tmp_path, schema_version) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    legacy = record().to_dict()
    if schema_version < 4:
        legacy.pop("position_before_atomic")
    if schema_version < 3:
        legacy.pop("operation_id")
    atomic_write_json(
        store.path,
        {"schema_version": schema_version, "records": [legacy]},
    )

    loaded = store.load()

    assert loaded == (record(),)


def test_fee_fields_are_public_decimal_strings_and_mapped_as_eth(tmp_path) -> None:
    item = record(
        max_total_fee_wei="500000000000000",
        actual_fee_wei="250000000000000",
    )
    store = HistoryStore(WalletPaths(tmp_path))
    store.append(item)
    mapped = history_record_to_map(store.load()[0])

    assert mapped["maxTotalFeeWei"] == "500000000000000"
    assert mapped["actualFeeWei"] == "250000000000000"
    assert mapped["maxFeeDisplay"].endswith("0.0005 ETH")
    assert mapped["actualFeeDisplay"] == "0.00025 ETH"


def test_v6_strictly_migrates_only_known_arbitrum_bridge2_funding(tmp_path) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    old = funding_record(action_type="transfer")
    atomic_write_json(store.path, {"schema_version": 6, "records": [
        old.to_dict(include_v5=True, include_v6=True),
    ]})
    loaded = store.load()
    assert loaded[0].action_type == "perpdex_funding"
    assert '"schema_version": 7' in store.path.read_text(encoding="utf-8")

    other = HistoryStore(WalletPaths(tmp_path / "other"))
    uncertain = funding_record(action_type="transfer", recipient=RECIPIENT)
    atomic_write_json(other.path, {"schema_version": 6, "records": [
        uncertain.to_dict(include_v5=True, include_v6=True),
    ]})
    assert other.load()[0].action_type == "transfer"


def test_only_old_unsigned_failed_perpdex_funding_is_pruned(tmp_path) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    stale = funding_record(status=HistoryStatus.FAILED, updated_at="2026-07-20T12:00:00Z")
    unknown = funding_record(2, status=HistoryStatus.UNKNOWN, updated_at="2026-07-20T12:00:00Z")
    submitted = funding_record(3, status=HistoryStatus.PENDING, transaction_hash=HASH,
                               updated_at="2026-07-20T12:00:00Z")
    store.append(stale); store.append(unknown); store.append(submitted)
    retained = store.prune_transient_perpdex_funding(datetime(2026, 7, 22, tzinfo=UTC))
    assert [item.action_id for item in retained] == ["act-2", "act-3"]
    mapped = history_record_to_map(retained[0])
    assert mapped["summaryTitle"] == "Deposit to Hyperliquid"
    assert mapped["networkLabel"] == "Arbitrum One"
    assert mapped["isPerpDex"] is True


@pytest.mark.parametrize(
    ("action_type", "title", "amount_label"),
    [
        ("lending_approve", "Approved Aave V3", "1 USDC allowance"),
        ("lending_supply", "Supplied to Aave V3", "1 USDC"),
        ("lending_withdraw", "Withdrawn from Aave V3", "+1 USDC"),
        ("lending_withdraw_all", "Withdrawn from Aave V3", "+1 USDC"),
        ("transfer", "Sent USDC", "−1 USDC"),
    ],
)
def test_history_uses_action_semantics_and_only_transfer_is_negative(
    action_type: str, title: str, amount_label: str,
) -> None:
    mapped = history_record_to_map(record(
        action_type=action_type,
        contract="0x" + "33" * 20 if action_type.startswith("lending_") else None,
    ))

    assert mapped["summaryTitle"] == title
    assert mapped["amountLabel"] == amount_label
    assert mapped["shortRecipient"] == (
        "Aave V3" if action_type.startswith("lending_") else "0x222222…222222"
    )


def test_zero_delta_all_withdraw_is_not_a_verified_cashflow() -> None:
    item = record(
        action_type="lending_withdraw_all",
        contract="0x" + "44" * 20,
        status=HistoryStatus.CONFIRMED,
        protocol_id="aave-v3",
        position_before_atomic="0",
        position_after_atomic="0",
        position_verified=True,
    )

    cashflows = lending_cashflows((item,), item.profile_id)

    assert cashflows == [{
        "action_id": item.action_id,
        "protocol": "aave-v3",
        "direction": "withdraw",
        "amount_atomic": None,
        "verified": True,
        "updated_at": item.updated_at,
    }]


def test_duplicate_unknown_and_invalid_transition_are_refused(tmp_path) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    store.append(record())
    with pytest.raises(HistoryValidationError, match="already exists"):
        store.append(record())
    with pytest.raises(HistoryValidationError, match="unknown"):
        store.update_status("act-missing", HistoryStatus.PENDING, "2026-07-20T12:01:00Z")

    store.update_status("act-1", HistoryStatus.FAILED, "2026-07-20T12:01:00Z")
    with pytest.raises(HistoryValidationError, match="transition"):
        store.update_status("act-1", HistoryStatus.PENDING, "2026-07-20T12:02:00Z")


def test_history_is_trimmed_to_latest_500_records(tmp_path) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    initial = [record(index) for index in range(MAX_HISTORY_RECORDS)]
    atomic_write_json(
        store.path,
        {
            "schema_version": HISTORY_SCHEMA_VERSION,
            "records": [
                item.to_dict(include_v5=True, include_v6=True) for item in initial
            ],
        },
    )

    stored = store.append(record(MAX_HISTORY_RECORDS))

    assert len(stored) == MAX_HISTORY_RECORDS
    assert stored[0].action_id == "act-1"
    assert stored[-1].action_id == f"act-{MAX_HISTORY_RECORDS}"


def test_corrupt_or_unsupported_history_is_never_replaced(tmp_path) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    atomic_write_json(store.path, {"schema_version": 999, "records": []})
    before = store.path.read_bytes()

    with pytest.raises(HistoryUnavailableError):
        store.load()
    with pytest.raises(HistoryUnavailableError):
        store.append(record())
    assert store.path.read_bytes() == before


def test_replace_failure_preserves_previous_history(tmp_path, monkeypatch) -> None:
    store = HistoryStore(WalletPaths(tmp_path))
    store.append(record())
    before = store.path.read_bytes()

    def fail_replace(*_args) -> None:
        raise OSError("injected")

    monkeypatch.setattr("holon_wallet.storage.os.replace", fail_replace)
    with pytest.raises(StorageError):
        store.append(record(2))
    assert store.path.read_bytes() == before


def test_simulated_record_is_explicit_in_qml_map(tmp_path) -> None:
    item = record(simulated=True, amount_atomic="2500000")
    mapped = history_record_to_map(item)

    assert mapped["simulated"] is True
    assert mapped["amount"] == "2.5 USDC"
    assert mapped["networkLabel"] == "Base"
    assert mapped["dateLabel"] == "Jul 20, 2026"


@pytest.mark.parametrize(
    "changes",
    [
        {"network": "arbitrum"},
        {"chain_id": 1},
        {"recipient": "not-an-address"},
        {"token": "USDT"},
        {"amount_atomic": "1.0"},
        {"decimals": 18},
        {"transaction_hash": "0x1234"},
        {"created_at": "not-utc"},
        {"max_total_fee_wei": "1.2"},
        {"actual_fee_wei": "-1"},
        {"receipt_code": "RAW_PROVIDER_EXCEPTION"},
        {"receipt_endpoint_class": "https://secret.invalid/token"},
    ],
)
def test_invalid_public_record_fields_are_refused(changes) -> None:
    with pytest.raises(HistoryValidationError):
        record(**changes)
