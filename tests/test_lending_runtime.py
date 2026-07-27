from __future__ import annotations

import copy
from decimal import Decimal

import pytest

from holon_contracts import ContractViolation, MessageKind, make_envelope
from holon_guard_ipc.codec import encode_message, make_response
from holon_lending import BASE_USDC, LendingReadService, ReadProfilesState
from holon_lending.runtime import ChainSnapshot, _freshness

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

    def begin(self) -> ChainSnapshot:
        self.calls.append("begin")
        if "begin" in self.fail:
            raise RuntimeError("offline")
        return ChainSnapshot(BLOCK, NOW - 10)

    def aave(self, snapshot: ChainSnapshot, account: str | None = None):
        del snapshot
        self.calls.append("aave")
        if "aave" in self.fail:
            raise RuntimeError("bad aave")
        return 40_000_000_000_000_000_000_000_000, None if account is None else 0

    def compound(self, snapshot: ChainSnapshot, account: str | None = None):
        del snapshot
        self.calls.append("compound")
        if "compound" in self.fail:
            raise RuntimeError("bad compound")
        return 800_000_000_000_000_000, 1_000_000_000, None if account is None else 1_250_000

    def morpho(self, snapshot: ChainSnapshot, account: str | None = None):
        del snapshot
        self.calls.append("morpho")
        if "morpho" in self.fail:
            raise RuntimeError("bad morpho")
        return None if account is None else 2_500_001


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
