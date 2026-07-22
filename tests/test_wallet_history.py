from __future__ import annotations

from dataclasses import replace

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
)
from holon_wallet.storage import StorageError, WalletPaths, atomic_write_json


SENDER = "0x" + "11" * 20
RECIPIENT = "0x" + "22" * 20
HASH = "0x" + "33" * 32


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
    atomic_write_json(store.path, {"schema_version": 1, "records": [legacy]})

    loaded = store.load()
    assert loaded[0].max_total_fee_wei is None
    assert loaded[0].actual_fee_wei is None
    assert '"schema_version": 1' in store.path.read_text(encoding="utf-8")

    store.update_status(
        "act-1", HistoryStatus.PENDING, "2026-07-20T12:01:00Z", HASH,
    )
    migrated = store.path.read_text(encoding="utf-8")
    assert '"schema_version": 2' in migrated
    assert '"max_total_fee_wei": null' in migrated


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
            "records": [item.to_dict() for item in initial],
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
    ],
)
def test_invalid_public_record_fields_are_refused(changes) -> None:
    with pytest.raises(HistoryValidationError):
        record(**changes)
