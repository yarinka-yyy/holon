from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path

import pytest
from web3 import Web3

from holon_lending import (
    AAVE_CONTRACTS,
    BASE_CHAIN_ID,
    BASE_USDC,
    COMPOUND_CONTRACTS,
    LendingReadProfiles,
    MORPHO_BLUE_ADDRESS,
    MORPHO_VAULT_ADDRESS,
    READ_PROFILES_DIGEST,
    READ_PROFILES_PATH,
    ReadProfilesLoadError,
    ReadProfilesState,
    ReadProfilesValidationError,
    canonical_read_profiles_bytes,
    load_read_profiles,
)


def profile_value() -> dict:
    return json.loads(READ_PROFILES_PATH.read_text(encoding="utf-8"))


def write_canonical(path: Path, value: dict) -> None:
    path.write_bytes(canonical_read_profiles_bytes(value))


def test_production_read_profiles_are_canonical_pinned_and_exact() -> None:
    raw = READ_PROFILES_PATH.read_bytes()
    value = profile_value()
    profiles = load_read_profiles()

    assert raw == canonical_read_profiles_bytes(value)
    assert hashlib.sha256(raw).hexdigest() == READ_PROFILES_DIGEST
    assert profiles == LendingReadProfiles.from_dict(value)
    assert BASE_CHAIN_ID == 8453
    assert BASE_USDC == "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
    assert value["asset"] == {
        "address": BASE_USDC, "asset_id": "usdc", "decimals": 6,
    }
    assert dict(profiles.protocols[0].contracts) == AAVE_CONTRACTS
    assert dict(profiles.protocols[1].contracts) == COMPOUND_CONTRACTS
    assert all(Web3.is_checksum_address(address) for profile in profiles.protocols
               for _, address in profile.contracts)


def test_morpho_profile_selects_exact_read_only_v1_vault() -> None:
    morpho = profile_value()["morpho_discovery"]
    assert {key: morpho[key] for key in ("collections", "endpoint", "filters")} == {
        "collections": ["vaults", "vaultV2s"],
        "endpoint": "https://api.morpho.org/graphql",
        "filters": {
            "asset_address": BASE_USDC,
            "asset_decimals": 6,
            "chain_id": BASE_CHAIN_ID,
            "listed": True,
        },
    }
    selected = morpho["selected_vault"]
    assert selected["address"] == MORPHO_VAULT_ADDRESS
    assert selected["morpho_address"] == MORPHO_BLUE_ADDRESS
    assert selected["vault_version"] == "v1"
    assert selected["vault_standard"] == "erc4626"
    assert selected["share_decimals"] == 18
    assert selected["name"] == "Gauntlet USDC Prime"
    assert selected["symbol"] == "gtUSDCp"
    assert selected["expected_config"]["fee_wad"] == "0"
    assert selected["expected_config"]["timelock_seconds"] == 604800
    assert all(Web3.is_checksum_address(selected["expected_config"][field])
               for field in ("owner", "curator", "guardian"))


def test_morpho_selection_evidence_is_historical_strict_and_complete() -> None:
    selected = profile_value()["morpho_discovery"]["selected_vault"]
    evidence = selected["selection_evidence"]
    allocations = evidence["allocations"]
    assert evidence["snapshot_only"] is True
    assert evidence["listed"] is True and evidence["featured"] is True
    assert evidence["warnings"] == []
    assert int(evidence["total_assets_atomic"]) >= 100_000_000 * 10**6
    assert int(evidence["liquidity_atomic"]) >= 25_000_000 * 10**6
    assert len(allocations) == 8
    assert [item["withdraw_queue_index"] for item in allocations] == list(range(8))
    assert len({item["market_id"] for item in allocations}) == 8
    assert sorted(item["supply_queue_index"] for item in allocations
                  if item["supply_queue_index"] is not None) == [0, 1]
    assert all(item["collateral_address"] is None
               or Web3.is_checksum_address(item["collateral_address"])
               for item in allocations)


def test_selection_snapshot_is_not_a_live_rate_or_write_authority() -> None:
    selected = profile_value()["morpho_discovery"]["selected_vault"]
    evidence = selected["selection_evidence"]
    semantics = selected["read_semantics"]
    assert not {"apy", "rate", "authority", "status"}.intersection(evidence)
    assert semantics["rate"] == "morpho_api.state.netApyExcludingRewards"
    assert semantics["position"] == "erc4626.balanceOf+convertToAssets"
    assert all(word not in semantics["position"] for word in ("deposit", "approve", "redeem"))


def test_missing_corrupt_noncanonical_incompatible_and_tampered_files(tmp_path: Path) -> None:
    path = tmp_path / "read-profiles.json"
    with pytest.raises(ReadProfilesLoadError, match="unavailable") as missing:
        load_read_profiles(path)
    assert missing.value.code == "READ_PROFILES_MISSING"

    path.write_text("{broken", encoding="utf-8")
    with pytest.raises(ReadProfilesLoadError) as corrupt:
        load_read_profiles(path)
    assert corrupt.value.code == "READ_PROFILES_CORRUPT"

    value = profile_value()
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    with pytest.raises(ReadProfilesLoadError) as noncanonical:
        load_read_profiles(path)
    assert noncanonical.value.code == "READ_PROFILES_CORRUPT"

    incompatible = dict(value, schema_version="2")
    write_canonical(path, incompatible)
    with pytest.raises(ReadProfilesLoadError) as version:
        load_read_profiles(path)
    assert version.value.code == "READ_PROFILES_INCOMPATIBLE"

    tampered = copy.deepcopy(value)
    tampered["sources"][0]["revision"] = "changed"
    write_canonical(path, tampered)
    with pytest.raises(ReadProfilesLoadError) as integrity:
        load_read_profiles(path)
    assert integrity.value.code == "READ_PROFILES_INTEGRITY_FAILED"


def test_windows_checkout_line_ending_preserves_canonical_digest(tmp_path: Path) -> None:
    path = tmp_path / "read-profiles.json"
    path.write_bytes(READ_PROFILES_PATH.read_bytes().replace(b"\n", b"\r\n"))
    assert load_read_profiles(path) == load_read_profiles()


def test_profile_failure_is_unavailable_without_rate_or_address_fallback(tmp_path: Path) -> None:
    state = ReadProfilesState.load(tmp_path / "missing.json")
    assert state.status == "UNAVAILABLE"
    assert state.error_code == "READ_PROFILES_MISSING"
    assert state.profiles is None


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value.update(extra="unknown"),
        lambda value: value.update(authority_enabled=False),
        lambda value: value["network"].update(chain_id=1),
        lambda value: value["asset"].update(address="0x0000000000000000000000000000000000000000"),
        lambda value: value["asset"].update(address=BASE_USDC.lower()),
        lambda value: value["asset"].update(decimals=18),
        lambda value: value["protocols"][0].update(protocol_id="unknown"),
        lambda value: value["protocols"].reverse(),
        lambda value: value["protocols"][0]["contracts"].update(extra=BASE_USDC),
        lambda value: value["morpho_discovery"]["filters"].update(listed=False),
        lambda value: value["morpho_discovery"].update(selected_vault=BASE_USDC),
        lambda value: value["morpho_discovery"]["selected_vault"].update(
            address="0x1111111111111111111111111111111111111111"),
        lambda value: value["morpho_discovery"]["selected_vault"].update(vault_version="v2"),
        lambda value: value["morpho_discovery"]["selected_vault"]["expected_config"].update(
            fee_wad="1"),
        lambda value: value["morpho_discovery"]["selected_vault"]["selection_evidence"].update(
            snapshot_only=False),
        lambda value: value["morpho_discovery"]["selected_vault"]["selection_evidence"].update(
            warnings=[{"level": "YELLOW"}]),
        lambda value: value["morpho_discovery"]["selected_vault"]["selection_evidence"].update(
            liquidity_atomic="24999999999999"),
        lambda value: value["morpho_discovery"]["selected_vault"]["selection_evidence"].update(
            observed_at="not-a-timeZ"),
        lambda value: value["morpho_discovery"]["selected_vault"]["selection_evidence"][
            "allocations"].reverse(),
    ],
)
def test_schema_rejects_unknown_authority_or_changed_identity(mutate) -> None:
    value = profile_value()
    mutate(value)
    with pytest.raises(ReadProfilesValidationError):
        LendingReadProfiles.from_dict(value)


def test_lending_runtime_stays_out_of_hermes_and_wallet() -> None:
    source_root = Path(__file__).parents[1] / "src"
    for package in ("holon_hermes_plugin", "holon_wallet"):
        for path in (source_root / package).glob("*.py"):
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            imported: set[str] = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    imported.add(node.module.split(".")[0])
            assert "holon_lending" not in imported


def test_only_guard_runtime_imports_lending() -> None:
    source_root = Path(__file__).parents[1] / "src" / "holon_guard"
    importing = []
    for path in source_root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if any(isinstance(node, ast.ImportFrom) and node.module == "holon_lending"
               for node in ast.walk(tree)):
            importing.append(path.name)
    assert sorted(importing) == ["__main__.py", "authority.py"]


def test_read_profiles_contain_no_authority_fields() -> None:
    raw = READ_PROFILES_PATH.read_text(encoding="utf-8")
    forbidden = (
        "authority_enabled", "action_type", "amount_atomic", "max_total_fee_wei",
        "private_key", "rpc_url", "signing", "broadcast",
    )
    assert all(field not in raw for field in forbidden)
