from __future__ import annotations

import copy
import json
from decimal import Decimal

import pytest

from holon_contracts import ContractViolation, MessageKind, make_envelope
from holon_guard_ipc.codec import encode_message, make_response
from holon_lending import (
    BASE_USDC, LendingAnalyticsStore, LendingPortfolioService,
    LendingReadService, ReadProfilesState,
)
from holon_lending.runtime import (
    ChainSnapshot, LendingTransportError, LendingWrongChainError, _freshness,
)

NOW = 1_800_000_000
BLOCK = 50_000_000
ACCOUNT = {
    "label": "Main Account",
    "address": "0x1111111111111111111111111111111111111111",
}


class FakeRpc:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.fail: set[str] = set()
        self.aave_position = 0
        self.compound_position = 1_250_000
        self.morpho_position = 2_500_001
        self.timestamp = NOW - 10

    def begin(self) -> ChainSnapshot:
        self.calls.append("begin")
        if "begin" in self.fail:
            raise RuntimeError("offline")
        return ChainSnapshot(BLOCK, self.timestamp)

    def aave(self, snapshot: ChainSnapshot, account: str | None = None):
        del snapshot
        self.calls.append("aave")
        if "aave" in self.fail:
            raise RuntimeError("bad aave")
        return (
            40_000_000_000_000_000_000_000_000,
            None if account is None else self.aave_position,
        )

    def compound(self, snapshot: ChainSnapshot, account: str | None = None):
        del snapshot
        self.calls.append("compound")
        if "compound" in self.fail:
            raise RuntimeError("bad compound")
        return (
            800_000_000_000_000_000, 1_000_000_000,
            None if account is None else self.compound_position,
        )

    def morpho(self, snapshot: ChainSnapshot, account: str | None = None):
        del snapshot
        self.calls.append("morpho")
        if "morpho" in self.fail:
            raise RuntimeError("bad morpho")
        return None if account is None else self.morpho_position


class FakeMorpho:
    def __init__(self) -> None:
        self.calls = 0
        self.value = {
            "address": "0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61",
            "name": "Gauntlet USDC Prime",
            "symbol": "gtUSDCp",
            "listed": True,
            "featured": True,
            "warnings": [],
            "asset": {"address": BASE_USDC, "decimals": 6},
            "state": {
                "netApyExcludingRewards": Decimal("0.045"),
                "timestamp": NOW - 30,
                "blockNumber": BLOCK - 10,
                "allRewards": [{
                    "asset": {
                        "address": "0x4200000000000000000000000000000000000006",
                        "chain": {"id": 8453},
                    },
                    "supplyApr": Decimal("0.0025"),
                }],
            },
        }

    def query(self):
        self.calls += 1
        return copy.deepcopy(self.value)


class TransportFailingRpc(FakeRpc):
    def aave(self, snapshot: ChainSnapshot, account: str | None = None):
        del snapshot, account
        self.calls.append("aave")
        raise LendingTransportError("provider unavailable")


class WrongChainRpc(FakeRpc):
    def begin(self) -> ChainSnapshot:
        self.calls.append("begin")
        raise LendingWrongChainError("wrong chain")


def refresh_fake_time(rpc: FakeRpc, morpho: FakeMorpho, now: int) -> None:
    rpc.timestamp = now - 10
    morpho.value["state"]["timestamp"] = now - 30


@pytest.fixture
def runtime():
    rpc, morpho = FakeRpc(), FakeMorpho()
    service = LendingReadService(
        ReadProfilesState.load(), lambda: rpc, morpho, clock=lambda: NOW,
    )
    return service, rpc, morpho


def test_compare_normalizes_three_protocols_and_rewards(runtime) -> None:
    service, rpc, morpho = runtime
    result = service.compare()
    assert result["status"] == "READY"
    assert result["authority_available"] is False
    assert [item["protocol"] for item in result["markets"]] == [
        "aave-v3", "compound-v3", "morpho-v1",
    ]
    aave, compound, selected = result["markets"]
    assert aave["base_yield"]["metric"] == "APR"
    assert aave["base_yield"]["value_percent"] == "4"
    assert Decimal(aave["base_yield"]["comparison_apy_percent"]) > Decimal("4")
    assert compound["base_yield"]["source_raw_unit"] == "per_second_wad"
    assert compound["incentives"]["status"] == "UNAVAILABLE"
    assert selected["base_yield"]["metric"] == "APY"
    assert selected["base_yield"]["comparison_apy_percent"] == "4.5"
    assert selected["incentives"]["total_apr_percent"] == "0.25"
    assert result["highest_observed"] == {
        "protocol": "morpho-v1",
        "comparison_apy_percent": "4.5",
        "not_safety_recommendation": True,
    }
    assert selected["confirmed_total_annual_percent"] == "4.75"
    assert selected["total_completeness"] == "BASE_AND_INCENTIVES"
    assert result["recommendation"] == {
        "protocol": "morpho-v1",
        "confirmed_total_annual_percent": "4.75",
        "missing_incentive_protocols": ["aave-v3", "compound-v3"],
        "incomplete_comparison": True,
        "requires_user_confirmation": True,
    }
    assert result["delivery"] == {
        "fetched_at": "2027-01-15T08:00:00Z", "cache_age_seconds": 0,
        "cache_max_age_seconds": 30, "force_refreshed": False,
    }
    assert rpc.calls.count("aave") == 1
    assert morpho.calls == 1


def test_positions_keep_exact_zero_and_atomic_amounts(runtime) -> None:
    service, _rpc, _morpho = runtime
    result = service.positions(ACCOUNT)
    assert result["status"] == "READY"
    assert result["account"] == ACCOUNT
    assert [item["amount_atomic"] for item in result["positions"]] == [
        "0", "1250000", "2500001",
    ]
    assert [item["display_amount"] for item in result["positions"]] == [
        "0 USDC", "1.25 USDC", "2.500001 USDC",
    ]


def test_one_protocol_failure_is_partial_not_zero(runtime) -> None:
    service, rpc, _morpho = runtime
    rpc.fail.add("compound")
    result = service.compare()
    assert result["status"] == "PARTIAL"
    failed = result["markets"][1]
    assert failed["base_yield"] is None
    assert failed["freshness"]["state"] == "UNAVAILABLE"
    assert failed["caveats"] == ["COMPOUND_DATA_UNAVAILABLE"]


def test_current_reads_fall_back_only_after_transport_failure() -> None:
    primary, fallback, morpho = TransportFailingRpc(), FakeRpc(), FakeMorpho()
    service = LendingReadService(
        ReadProfilesState.load(), lambda: primary, morpho, clock=lambda: NOW,
        rpc_fallback_factories=(lambda: fallback,),
    )

    compared = service.compare(True)
    positions = service.positions(ACCOUNT)

    assert compared["status"] == "READY"
    assert positions["status"] == "READY"
    assert primary.calls.count("aave") >= 2
    assert fallback.calls.count("aave") >= 2


def test_wrong_chain_does_not_try_lending_read_fallback() -> None:
    primary, fallback, morpho = WrongChainRpc(), FakeRpc(), FakeMorpho()
    service = LendingReadService(
        ReadProfilesState.load(), lambda: primary, morpho, clock=lambda: NOW,
        rpc_fallback_factories=(lambda: fallback,),
    )

    compared = service.compare(True)
    positions = service.positions(ACCOUNT)

    assert compared["status"] == "DEGRADED"
    assert positions["status"] == "DEGRADED"
    assert fallback.calls == []


def test_compare_reuses_thirty_second_cache_and_force_refreshes() -> None:
    now = [NOW]
    rpc, morpho = FakeRpc(), FakeMorpho()
    service = LendingReadService(
        ReadProfilesState.load(), lambda: rpc, morpho, clock=lambda: now[0],
    )
    first = service.compare()
    calls = list(rpc.calls)
    now[0] += 30
    cached = service.compare()
    assert rpc.calls == calls
    assert cached["delivery"]["cache_age_seconds"] == 30
    rpc.fail.add("compound")
    refreshed = service.compare(True)
    assert refreshed["status"] == "PARTIAL"
    assert refreshed["delivery"]["force_refreshed"] is True
    now[0] += 31
    rpc.fail.clear()
    assert service.compare()["status"] == "READY"


def test_profile_failure_performs_no_network_call() -> None:
    calls = []
    service = LendingReadService.unavailable("READ_PROFILES_INTEGRITY_FAILED")
    service._rpc_factory = lambda: calls.append("rpc")  # type: ignore[method-assign]
    result = service.compare()
    assert result["code"] == "LENDING_UNAVAILABLE"
    assert calls == []
    assert all(item["caveats"] == ["READ_PROFILES_INTEGRITY_FAILED"] for item in result["markets"])


def test_missing_account_does_not_open_rpc(runtime) -> None:
    service, rpc, _morpho = runtime
    result = service.positions(None)
    assert result["code"] == "LENDING_POSITIONS_UNAVAILABLE"
    assert rpc.calls == []


def test_morpho_warning_or_malformed_reward_is_unavailable(runtime) -> None:
    service, _rpc, morpho = runtime
    morpho.value["warnings"] = [{"type": "UNKNOWN", "level": "WARNING"}]
    result = service.compare()
    assert result["markets"][2]["caveats"] == ["MORPHO_DATA_UNAVAILABLE"]
    morpho.value["warnings"] = []
    morpho.value["state"]["allRewards"] = [{}]
    result = service.compare()
    assert result["markets"][2]["base_yield"] is None


@pytest.mark.parametrize(
    ("age", "api", "state"),
    [
        (120, False, "LIVE"), (121, False, "STALE"), (900, False, "STALE"),
        (901, False, "UNAVAILABLE"), (300, True, "LIVE"),
        (301, True, "STALE"), (1800, True, "STALE"),
        (1801, True, "UNAVAILABLE"), (-61, False, "UNAVAILABLE"),
    ],
)
def test_freshness_boundaries(age: int, api: bool, state: str) -> None:
    assert _freshness(NOW - age, BLOCK, NOW, api=api)["state"] == state


def test_contracts_accept_runtime_payloads_and_reject_nested_mutation(runtime) -> None:
    service, _rpc, _morpho = runtime
    markets = make_envelope(MessageKind.LENDING_MARKETS, service.compare())
    positions = make_envelope(MessageKind.LENDING_POSITIONS, service.positions(ACCOUNT))
    assert len(encode_message(make_response(markets))) < 8 * 1024
    assert len(encode_message(make_response(positions))) < 8 * 1024
    invalid = markets.to_dict()
    invalid["payload"]["markets"][0]["base_yield"]["metric"] = "UNKNOWN"
    with pytest.raises(ContractViolation):
        from holon_contracts import parse_envelope
        parse_envelope(invalid)


def test_portfolio_tracks_positions_earnings_cache_and_history(tmp_path) -> None:
    now = [NOW]
    rpc, morpho = FakeRpc(), FakeMorpho()
    reader = LendingReadService(
        ReadProfilesState.load(), lambda: rpc, morpho, clock=lambda: now[0],
    )
    service = LendingPortfolioService(
        reader, LendingAnalyticsStore(tmp_path / "analytics.json"),
        clock=lambda: now[0],
    )
    first = service.read(ACCOUNT, [], history_period="all")
    assert first["summary"]["total_position_atomic"] == "3750001"
    assert first["summary"]["tracked_earnings_atomic"] == "0"
    assert first["summary"]["yield_completeness"] == "COMPLETE"
    assert len(first["history"]["points"]) == 1

    now[0] += 61
    rpc.compound_position = 1_550_000
    operations = [{
        "action_id": "supply-1", "protocol": "compound-v3",
        "direction": "supply", "amount_atomic": "250000",
        "verified": True, "updated_at": first["delivery"]["fetched_at"],
    }]
    second = service.read(
        ACCOUNT, operations, force_refresh=True, history_period="all",
    )
    compound = second["protocols"][1]
    assert compound["tracked_earnings_atomic"] == "50000"
    assert second["summary"]["tracked_earnings_atomic"] == "50000"
    assert len(second["history"]["points"]) == 1
    assert second["history"]["granularity"] == "day"

    calls = len(rpc.calls)
    now[0] += 30
    cached = service.read(ACCOUNT, operations)
    assert len(rpc.calls) == calls
    assert cached["delivery"]["source"] == "MEMORY_CACHE"


def test_portfolio_tracks_partial_and_full_withdrawal_and_negative_earnings(tmp_path) -> None:
    now = [NOW]
    rpc, morpho = FakeRpc(), FakeMorpho()
    rpc.aave_position = 1_000_000
    service = LendingPortfolioService(
        LendingReadService(
            ReadProfilesState.load(), lambda: rpc, morpho, clock=lambda: now[0],
        ),
        LendingAnalyticsStore(tmp_path / "analytics.json"),
        clock=lambda: now[0],
    )
    service.read(ACCOUNT, [])
    now[0] += 61
    rpc.compound_position = 1_550_000
    operations = [{
        "action_id": "supply", "protocol": "compound-v3", "direction": "supply",
        "amount_atomic": "250000", "verified": True,
        "updated_at": "2027-01-15T08:00:00Z",
    }]
    service.read(ACCOUNT, operations, force_refresh=True)

    now[0] += 61
    rpc.compound_position = 1_050_000
    operations.append({
        "action_id": "partial-withdraw", "protocol": "compound-v3",
        "direction": "withdraw", "amount_atomic": "500000", "verified": True,
        "updated_at": "2027-01-15T08:01:00Z",
    })
    partial = service.read(ACCOUNT, operations, force_refresh=True)
    assert partial["protocols"][1]["tracked_earnings_atomic"] == "50000"

    now[0] += 61
    rpc.compound_position = 0
    operations.append({
        "action_id": "full-withdraw", "protocol": "compound-v3",
        "direction": "withdraw", "amount_atomic": "1050000", "verified": True,
        "updated_at": "2027-01-15T08:02:00Z",
    })
    complete = service.read(ACCOUNT, operations, force_refresh=True)
    assert complete["protocols"][1]["tracked_earnings_atomic"] == "50000"

    now[0] += 61
    rpc.aave_position = 999_999
    negative = service.read(ACCOUNT, operations, force_refresh=True)
    assert negative["protocols"][0]["tracked_earnings_atomic"] == "-1"
    assert negative["protocols"][0]["display_tracked_earnings"] == "−0.000001 USDC"


def test_portfolio_marks_earnings_unknown_without_reliable_wallet_history(tmp_path) -> None:
    service, _rpc, _morpho = runtime.__wrapped__()  # type: ignore[attr-defined]
    result = LendingPortfolioService(
        service, LendingAnalyticsStore(tmp_path / "analytics.json"), clock=lambda: NOW,
    ).read(ACCOUNT, None)
    assert result["summary"]["earnings_status"] == "NOT_ENOUGH_HISTORY"
    assert all(item["earnings_status"] == "NOT_ENOUGH_HISTORY" for item in result["protocols"])


def test_portfolio_rechecks_invalid_retained_cashflow_before_showing_earnings(tmp_path) -> None:
    now = [NOW]
    rpc, morpho = FakeRpc(), FakeMorpho()
    rpc.aave_position = 1_000_000
    store = LendingAnalyticsStore(tmp_path / "analytics.json")
    portfolio = LendingPortfolioService(
        LendingReadService(
            ReadProfilesState.load(), lambda: rpc, morpho, clock=lambda: now[0],
        ),
        store,
        clock=lambda: now[0],
    )
    portfolio.read(ACCOUNT, [])
    retained = store.load(ACCOUNT["address"])
    assert retained is not None
    retained["baselines"]["aave-v3"].update({
        "net_contributions_atomic": "0",
        "processed_action_ids": ["withdraw-all"],
        "history_complete": True,
    })
    assert store.save(retained)

    now[0] += 61
    rpc.aave_position = 0
    result = portfolio.read(ACCOUNT, [{
        "action_id": "withdraw-all", "protocol": "aave-v3",
        "direction": "withdraw", "amount_atomic": None, "verified": True,
        "updated_at": "2027-01-15T08:01:00Z",
    }], force_refresh=True)

    assert result["protocols"][0]["tracked_earnings_atomic"] is None
    assert result["protocols"][0]["earnings_status"] == "NOT_ENOUGH_HISTORY"


def test_portfolio_keeps_contribution_total_after_wallet_history_rotation(tmp_path) -> None:
    now = [NOW]
    rpc, morpho = FakeRpc(), FakeMorpho()
    rpc.aave_position = 1_000_000
    store = LendingAnalyticsStore(tmp_path / "analytics.json")
    reader = LendingReadService(
        ReadProfilesState.load(), lambda: rpc, morpho, clock=lambda: now[0],
    )
    portfolio = LendingPortfolioService(reader, store, clock=lambda: now[0])
    portfolio.read(ACCOUNT, [])

    now[0] += 86_400
    refresh_fake_time(rpc, morpho, now[0])
    rpc.aave_position = 1_000_100
    first = [{
        "action_id": "old-supply", "protocol": "aave-v3", "direction": "supply",
        "amount_atomic": "100", "verified": True,
        "updated_at": "2027-01-16T08:00:00Z",
    }]
    portfolio.read(ACCOUNT, first, force_refresh=True)
    first_baseline = store.load(ACCOUNT["address"])["baselines"]["aave-v3"]
    assert first_baseline["net_contributions_atomic"] == "100"
    assert first_baseline["processed_action_ids"] == ["old-supply"]

    now[0] += 86_400
    refresh_fake_time(rpc, morpho, now[0])
    rpc.aave_position = 1_000_300
    current_history = [{
        "action_id": "current-supply", "protocol": "aave-v3", "direction": "supply",
        "amount_atomic": "200", "verified": True,
        "updated_at": "2027-01-17T08:00:00Z",
    }]
    rotated = portfolio.read(ACCOUNT, current_history, force_refresh=True)
    baseline = store.load(ACCOUNT["address"])["baselines"]["aave-v3"]
    assert baseline["net_contributions_atomic"] == "300"
    assert baseline["processed_action_ids"] == ["current-supply"]
    assert rotated["protocols"][0]["tracked_earnings_atomic"] == "0"

    restarted = LendingPortfolioService(reader, store, clock=lambda: now[0])
    repeated = restarted.read(ACCOUNT, current_history, force_refresh=True)
    assert repeated["protocols"][0]["tracked_earnings_atomic"] == "0"
    assert store.load(ACCOUNT["address"])["baselines"]["aave-v3"][
        "net_contributions_atomic"
    ] == "300"


def test_portfolio_sparse_history_does_not_invent_missing_days(tmp_path) -> None:
    now = [NOW]
    service, rpc, morpho = runtime.__wrapped__()  # type: ignore[attr-defined]
    portfolio = LendingPortfolioService(
        service, LendingAnalyticsStore(tmp_path / "analytics.json"), clock=lambda: now[0],
    )
    portfolio.read(ACCOUNT, [], force_refresh=True)
    for days in (3, 12):
        now[0] += days * 86_400
        refresh_fake_time(rpc, morpho, now[0])
        result = portfolio.read(ACCOUNT, [], force_refresh=True, history_period="all")
    assert result["history"]["granularity"] == "ten_day"
    assert len(result["history"]["points"]) == 2

    now[0] += 25 * 86_400
    refresh_fake_time(rpc, morpho, now[0])
    monthly = portfolio.read(ACCOUNT, [], force_refresh=True, history_period="all")
    assert monthly["history"]["granularity"] == "month"
    assert len(monthly["history"]["points"]) == 2


def test_portfolio_replaces_same_day_and_compacts_old_days_to_months(tmp_path) -> None:
    now = [NOW]
    service, rpc, morpho = runtime.__wrapped__()  # type: ignore[attr-defined]
    store = LendingAnalyticsStore(tmp_path / "analytics.json")
    portfolio = LendingPortfolioService(service, store, clock=lambda: now[0])
    portfolio.read(ACCOUNT, [])
    now[0] += 30
    portfolio.read(ACCOUNT, [], force_refresh=True)
    state = store.load(ACCOUNT["address"])
    assert state is not None and len(state["daily_history"]) == 1
    assert state["daily_history"][0]["observation_count"] == 2

    for _ in range(35):
        now[0] += 86_400
        refresh_fake_time(rpc, morpho, now[0])
        portfolio.read(ACCOUNT, [], force_refresh=True)
    retained = store.load(ACCOUNT["address"])
    assert retained is not None and len(retained["daily_history"]) == 31
    assert len(retained["monthly_history"]) == 1

    seven = portfolio.read(ACCOUNT, [], history_period="7d")["history"]
    thirty = portfolio.read(ACCOUNT, [], history_period="30d")["history"]
    all_time = portfolio.read(ACCOUNT, [], history_period="all")["history"]
    assert seven["granularity"] == "day" and len(seven["points"]) == 7
    assert thirty["granularity"] == "ten_day" and len(thirty["points"]) == 3
    assert all_time["granularity"] == "month" and len(all_time["points"]) == 2


def test_portfolio_uses_persisted_protocol_fallback(tmp_path) -> None:
    now = [NOW]
    rpc, morpho = FakeRpc(), FakeMorpho()
    reader = LendingReadService(
        ReadProfilesState.load(), lambda: rpc, morpho, clock=lambda: now[0],
    )
    service = LendingPortfolioService(
        reader, LendingAnalyticsStore(tmp_path / "analytics.json"),
        clock=lambda: now[0],
    )
    service.read(ACCOUNT, [])
    now[0] += 31
    rpc.fail.add("compound")
    result = service.read(ACCOUNT, [], force_refresh=True)
    assert result["status"] == "PARTIAL"
    assert result["protocols"][1]["position_atomic"] == "1250000"
    assert result["protocols"][1]["data_state"] == "CACHED"
    assert "USING_CACHED_DATA" in result["protocols"][1]["caveats"]


def test_portfolio_observation_uses_oldest_actual_amount_and_rate_source(tmp_path) -> None:
    rpc, morpho = FakeRpc(), FakeMorpho()
    reader = LendingReadService(
        ReadProfilesState.load(), lambda: rpc, morpho, clock=lambda: NOW,
    )
    store = LendingAnalyticsStore(tmp_path / "analytics.json")
    portfolio = LendingPortfolioService(reader, store, clock=lambda: NOW)
    portfolio.read(ACCOUNT, [])
    stored = store.load(ACCOUNT["address"])
    assert stored is not None
    previous_at = "2027-01-14T08:00:00Z"
    stored["current"]["protocols"][1]["observed_at"] = previous_at

    markets = reader.compare(True)
    positions = reader.positions(ACCOUNT)
    rate_at = "2027-01-15T08:00:10Z"
    amount_at = "2027-01-15T08:00:20Z"
    markets["markets"][1]["freshness"]["observed_at"] = rate_at
    positions["positions"][1]["freshness"]["observed_at"] = amount_at

    def combined(*, live_rate: bool, live_amount: bool) -> dict[str, object]:
        market_payload = copy.deepcopy(markets)
        position_payload = copy.deepcopy(positions)
        market = market_payload["markets"][1]
        position = position_payload["positions"][1]
        if not live_rate:
            market.update({
                "base_yield": None,
                "incentives": {
                    "status": "UNAVAILABLE", "total_apr_percent": None,
                    "components": [],
                },
                "confirmed_total_annual_percent": None,
                "total_completeness": "UNAVAILABLE",
            })
            market["freshness"] = {
                "state": "UNAVAILABLE", "observed_at": None,
                "block_number": None,
            }
        if not live_amount:
            position["amount_atomic"] = None
            position["display_amount"] = None
            position["freshness"] = {
                "state": "UNAVAILABLE", "observed_at": None,
                "block_number": None,
            }
        return portfolio._combine(market_payload, position_payload, stored)[1]

    assert combined(live_rate=True, live_amount=True)["observed_at"] == rate_at
    assert combined(live_rate=True, live_amount=False)["observed_at"] == previous_at
    assert combined(live_rate=False, live_amount=True)["observed_at"] == previous_at
    cached = combined(live_rate=False, live_amount=False)
    assert cached["observed_at"] == previous_at
    assert cached["data_state"] == "CACHED"


def test_portfolio_contract_is_bounded(tmp_path) -> None:
    service, rpc, morpho = runtime.__wrapped__()  # type: ignore[attr-defined]
    now = [NOW]
    portfolio = LendingPortfolioService(
        service, LendingAnalyticsStore(tmp_path / "analytics.json"),
        clock=lambda: now[0],
    )
    for _ in range(14):
        result = portfolio.read(
            ACCOUNT, [], force_refresh=True, history_period="all", history_limit=12,
        )
        now[0] += 31 * 86_400
        refresh_fake_time(rpc, morpho, now[0])
    envelope = make_envelope(MessageKind.LENDING_PORTFOLIO, result)
    assert len(result["history"]["points"]) == 12
    assert result["history"]["granularity"] == "month"
    assert len(encode_message(make_response(envelope))) < 8 * 1024

    mutations: list[tuple[str, dict[str, object]]] = []
    for index in range(3):
        invalid = copy.deepcopy(result)
        invalid["protocols"][index]["contract_address"] = (
            "0x1111111111111111111111111111111111111111"
        )
        mutations.append((f"pinned-contract-{index}", invalid))
    for name, mutate in (
        ("display-name", lambda value: value["protocols"][0].update(display_name="Other")),
        ("position-display", lambda value: value["protocols"][0].update(display_position="999 USDC")),
        ("base-yield", lambda value: value["protocols"][0]["base_yield"].update(metric="UNKNOWN")),
        ("incentives", lambda value: value["protocols"][2]["incentives"].update(status="UNKNOWN")),
        ("completeness", lambda value: value["protocols"][0].update(total_completeness="BASE_AND_INCENTIVES")),
        ("earnings-display", lambda value: value["protocols"][0].update(display_tracked_earnings="wrong")),
        ("observation", lambda value: value["protocols"][0].update(observed_at=None)),
        ("summary", lambda value: value["summary"].update(display_total_position="wrong")),
    ):
        invalid = copy.deepcopy(result)
        mutate(invalid)
        mutations.append((name, invalid))
    for name, invalid in mutations:
        with pytest.raises(ContractViolation) as violation:
            make_envelope(MessageKind.LENDING_PORTFOLIO, invalid)
        assert violation.value.code == "REQUEST_INVALID", name


def test_portfolio_migrates_valid_schema_v1_atomically(tmp_path) -> None:
    path = tmp_path / "analytics.json"
    service, _rpc, _morpho = runtime.__wrapped__()  # type: ignore[attr-defined]
    original_store = LendingAnalyticsStore(path)
    current = LendingPortfolioService(service, original_store, clock=lambda: NOW).read(
        ACCOUNT, [], history_period="all",
    )
    state = original_store.load(ACCOUNT["address"])
    assert state is not None
    legacy = {
        "schema_version": 1,
        "accounts": [{
            "address": state["address"], "label": state["label"],
            "saved_at": state["saved_at"], "current": state["current"],
            "baselines": state["baselines"],
            "observations": [current["history"]["points"][0]],
        }],
    }
    path.write_text(json.dumps(legacy), encoding="utf-8")

    migrated = LendingAnalyticsStore(path).load(ACCOUNT["address"])
    persisted = json.loads(path.read_text(encoding="utf-8"))
    assert migrated is not None and len(migrated["daily_history"]) == 1
    assert persisted["schema_version"] == 2
    assert "observations" not in persisted["accounts"][0]


def test_portfolio_ignores_invalid_nested_persisted_state(tmp_path) -> None:
    path = tmp_path / "analytics.json"
    path.write_text(
        json.dumps({
            "schema_version": 1,
            "accounts": [{
                "address": ACCOUNT["address"], "label": ACCOUNT["label"],
                "saved_at": "broken", "current": {"protocols": [None, None, None]},
                "baselines": {}, "observations": [],
            }],
        }),
        encoding="utf-8",
    )
    service, _rpc, _morpho = runtime.__wrapped__()  # type: ignore[attr-defined]
    portfolio = LendingPortfolioService(
        service, LendingAnalyticsStore(path), clock=lambda: NOW,
    )
    assert portfolio.cached(ACCOUNT)["status"] == "DEGRADED"
    assert json.loads(path.read_text(encoding="utf-8"))["schema_version"] == 1


def test_portfolio_does_not_overwrite_damaged_schema_v2(tmp_path) -> None:
    path = tmp_path / "analytics.json"
    damaged = '{"schema_version": 2, "accounts": ['
    path.write_text(damaged, encoding="utf-8")
    service, _rpc, _morpho = runtime.__wrapped__()  # type: ignore[attr-defined]
    portfolio = LendingPortfolioService(
        service, LendingAnalyticsStore(path), clock=lambda: NOW,
    )

    result = portfolio.read(ACCOUNT, [], force_refresh=True, history_period="all")

    assert result["history"]["granularity"] == "none"
    assert path.read_text(encoding="utf-8") == damaged
