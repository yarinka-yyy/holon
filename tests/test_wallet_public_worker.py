from __future__ import annotations

from pathlib import Path
import threading
from unittest.mock import patch

from holon_contracts.payloads import validate_wallet_balances
from holon_wallet.model import ProfileSummary
from holon_wallet.public_data import PublicDataStatus
from holon_wallet.public_worker import read_active_balances
from holon_wallet.settings import SettingsStore
from holon_wallet.storage import WalletPaths
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic

from wallet_public_support import StubPublicDataService


def create_repository(tmp_path: Path) -> tuple[VaultRepository, SettingsStore, ProfileSummary]:
    paths = WalletPaths(tmp_path)
    repository = VaultRepository(paths)
    record = repository.new_record(generate_mnemonic(), "Main Account")
    repository.create_new("fixture-password", record)
    settings = SettingsStore(paths)
    settings.save_active_id(record.summary.profile_id)
    return repository, settings, record.summary


def test_live_worker_read_uses_public_header_without_authentication(tmp_path: Path) -> None:
    repository, settings, profile = create_repository(tmp_path)
    service = StubPublicDataService()
    with (
        patch.object(repository, "authenticate", side_effect=AssertionError("authenticate")),
        patch.object(repository, "authenticate_profile", side_effect=AssertionError("decrypt")),
    ):
        result = read_active_balances(repository, settings, service)
    validate_wallet_balances(result)
    assert result["status"] == "READY"
    assert result["authority_available"] is False
    assert result["account"] == {"label": "Main Account", "address": profile.address}
    assert [item["network"] for item in result["networks"]] == ["ethereum", "base"]
    assert all(item["status"] == "LIVE" for item in result["networks"])
    assert sorted(call[2][0] for call in service.calls) == ["base", "ethereum"]


def test_network_failures_are_independent_and_never_zero_fallback(tmp_path: Path) -> None:
    repository, settings, _profile = create_repository(tmp_path)
    service = StubPublicDataService({
        "ethereum": PublicDataStatus.UNAVAILABLE,
        "base": PublicDataStatus.LIVE,
    })
    result = read_active_balances(repository, settings, service)
    validate_wallet_balances(result)
    ethereum, base = result["networks"]
    assert result["status"] == "PARTIAL"
    assert ethereum["balances"] is None
    assert ethereum["error_code"] == "RPC_UNAVAILABLE"
    assert base["balances"]["ETH"]["amount_atomic"] == str(10**18)


def test_missing_and_malformed_vault_are_safe(tmp_path: Path) -> None:
    paths = WalletPaths(tmp_path)
    missing = read_active_balances(
        VaultRepository(paths), SettingsStore(paths), StubPublicDataService(),
    )
    assert missing["code"] == "WALLET_NOT_CREATED"
    validate_wallet_balances(missing)
    assert all(item["balances"] is None for item in missing["networks"])
    paths.data_dir.mkdir(parents=True, exist_ok=True)
    paths.vault.write_text("not json", encoding="utf-8")
    malformed = read_active_balances(
        VaultRepository(paths), SettingsStore(paths), StubPublicDataService(),
    )
    validate_wallet_balances(malformed)
    assert malformed["code"] == "WALLET_UNAVAILABLE"
    assert "not json" not in str(malformed)


def test_active_profile_mutation_refuses_mixed_snapshot() -> None:
    first = ProfileSummary(
        "11111111-1111-4111-8111-111111111111", "First",
        "0x1111111111111111111111111111111111111111", "mnemonic", None,
        "2026-07-23T12:00:00Z",
    )
    second = ProfileSummary(
        "22222222-2222-4222-8222-222222222222", "Second",
        "0x2222222222222222222222222222222222222222", "mnemonic", None,
        "2026-07-23T12:00:00Z",
    )

    class ChangingRepository:
        exists = True

        def __init__(self) -> None:
            self.calls = 0

        def load_public(self):
            self.calls += 1
            return (first,) if self.calls == 1 else (second,)

    class EmptySettings:
        def load_active_id(self, valid_ids):
            del valid_ids
            return None

    result = read_active_balances(
        ChangingRepository(), EmptySettings(), StubPublicDataService(),  # type: ignore[arg-type]
    )
    validate_wallet_balances(result)
    assert result["status"] == "DEGRADED"
    assert result["code"] == "BALANCES_UNAVAILABLE"
    assert result["account"] is None
    assert {item["error_code"] for item in result["networks"]} == {"ACCOUNT_CHANGED"}


def test_overall_deadline_marks_only_unfinished_networks_unavailable(
    tmp_path: Path,
) -> None:
    repository, settings, _profile = create_repository(tmp_path)
    release = threading.Event()

    class SlowService(StubPublicDataService):
        def refresh(self, profile_id, address, network_ids):
            if network_ids == ("ethereum",):
                release.wait(1.0)
            return super().refresh(profile_id, address, network_ids)

    with patch("holon_wallet.public_worker.READ_DEADLINE_SECONDS", 0.02):
        result = read_active_balances(repository, settings, SlowService())
    validate_wallet_balances(result)
    release.set()
    ethereum, base = result["networks"]
    assert result["status"] == "PARTIAL"
    assert ethereum["status"] == "UNAVAILABLE"
    assert ethereum["error_code"] == "RPC_TIMEOUT"
    assert base["status"] == "LIVE"


def test_worker_mode_bypasses_gui_and_normal_wallet_mutex() -> None:
    from holon_wallet import application, public_worker

    with (
        patch.object(public_worker, "run_public_balances_worker", return_value=7),
        patch.object(application, "WalletApplication", side_effect=AssertionError("GUI")),
        patch.object(application, "ProcessInstance", side_effect=AssertionError("mutex")),
    ):
        assert application.main(["--public-balances-worker"]) == 7
