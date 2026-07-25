from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from holon_lending import ACTION_PROFILES_DIGEST
from holon_lending.preflight import unavailable_preview
from holon_wallet import application, lending_worker
from holon_wallet.lending_worker import read_lending_preview
from holon_wallet.settings import SettingsStore
from holon_wallet.storage import WalletPaths
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic
from holon_wallet_control import ControlProtocolError
from holon_wallet_control.lending_preview import (
    WalletLendingPreviewServer,
    validate_request,
    validate_response,
)


def intent():
    return {
        "module_id": "lending", "module_version": "1",
        "protocol_profile_id": "aave-v3-base-usdc",
        "protocol_profile_version": "1", "network": "base", "asset": "usdc",
        "beneficiary_mode": "active_wallet_account", "action": "supply",
        "amount_mode": "exact", "amount": "1",
    }


def request():
    return {
        "preview_version": "1", "kind": "prepare_lending_preview",
        "correlation_id": "11111111-1111-4111-8111-111111111111",
        "profile_digest": ACTION_PROFILES_DIGEST, "intent": intent(),
    }


class FakeService:
    def __init__(self) -> None:
        self.calls = []

    def prepare(self, raw_intent, account, *, expected_profile_digest):
        self.calls.append((raw_intent, account, expected_profile_digest))
        return unavailable_preview(
            "BASE_RPC_UNAVAILABLE", requested_action="supply",
            amount_mode="exact", account=account,
            profile_digest=expected_profile_digest,
        )


def repository(tmp_path: Path):
    paths = WalletPaths(tmp_path)
    repo = VaultRepository(paths)
    record = repo.new_record(generate_mnemonic(), "Main Account")
    repo.create_new("fixture-password", record)
    settings = SettingsStore(paths)
    settings.save_active_id(record.summary.profile_id)
    return repo, settings, record.summary


def test_worker_uses_only_public_vault_header(tmp_path: Path) -> None:
    repo, settings, profile = repository(tmp_path)
    service = FakeService()
    with (
        patch.object(repo, "authenticate", side_effect=AssertionError("password path")),
        patch.object(repo, "authenticate_profile", side_effect=AssertionError("secret path")),
    ):
        result = read_lending_preview(request(), repo, settings, service)  # type: ignore[arg-type]
    assert result["status"] == "UNAVAILABLE"
    assert service.calls == [(
        intent(), {"label": "Main Account", "address": profile.address},
        ACTION_PROFILES_DIGEST,
    )]
    assert "password" not in str(result).lower()


def test_missing_wallet_never_calls_preflight(tmp_path: Path) -> None:
    paths = WalletPaths(tmp_path)

    class Never:
        def prepare(self, *args, **kwargs):
            raise AssertionError((args, kwargs))

    result = read_lending_preview(
        request(), VaultRepository(paths), SettingsStore(paths), Never(),  # type: ignore[arg-type]
    )
    assert result["caveats"] == ["WALLET_ACCOUNT_UNAVAILABLE"]


def test_local_pipe_schema_has_no_secret_or_policy_contents() -> None:
    checked = validate_request(request())
    assert set(checked) == {
        "preview_version", "kind", "correlation_id", "profile_digest", "intent",
    }
    unsafe = dict(request(), password="hidden")
    with pytest.raises(ControlProtocolError):
        validate_request(unsafe)
    preview = unavailable_preview(
        "BASE_RPC_UNAVAILABLE", requested_action="supply", amount_mode="exact",
        profile_digest=ACTION_PROFILES_DIGEST,
    )
    response = {
        "preview_version": "1", "kind": "lending_preview",
        "correlation_id": request()["correlation_id"], "wallet_pid": 202,
        "preview": preview,
    }
    assert validate_response(response, request(), 202)["preview"] == preview
    response["correlation_id"] = "22222222-2222-4222-8222-222222222222"
    with pytest.raises(ControlProtocolError):
        validate_response(response, request(), 202)


def test_worker_mode_bypasses_gui_and_mutex() -> None:
    with (
        patch.object(lending_worker, "run_lending_preview_worker", return_value=9),
        patch.object(application, "WalletApplication", side_effect=AssertionError("GUI")),
        patch.object(application, "ProcessInstance", side_effect=AssertionError("mutex")),
    ):
        assert application.main(["--lending-preview-worker"]) == 9


class ServerConnection:
    def __init__(self, incoming: bytes) -> None:
        self.incoming = incoming
        self.sent: list[bytes] = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def fileno(self):
        return 44

    def poll(self, timeout):
        del timeout
        return True

    def recv_bytes(self, maximum):
        assert len(self.incoming) <= maximum
        return self.incoming

    def send_bytes(self, value):
        self.sent.append(value)


class ServerListener:
    def __init__(self, connection: ServerConnection) -> None:
        self.connection = connection

    def accept(self):
        return self.connection

    def close(self):
        return None


def test_worker_server_verifies_guard_pid_and_exact_path(tmp_path: Path) -> None:
    raw = json.dumps(request(), separators=(",", ":")).encode()
    connection = ServerConnection(raw)
    expected = tmp_path / "HolonGuard.exe"
    preview = unavailable_preview(
        "BASE_RPC_UNAVAILABLE", requested_action="supply", amount_mode="exact",
        profile_digest=ACTION_PROFILES_DIGEST,
    )
    server = WalletLendingPreviewServer(
        lambda value: preview,
        listener_factory=lambda *args, **kwargs: ServerListener(connection),
        wallet_pid=lambda: 202, peer_pid=lambda handle: 303,
        process_image=lambda pid: expected, expected_guard_path=expected,
    )
    server.serve_once()
    assert connection.sent

    rejected = ServerConnection(raw)
    server = WalletLendingPreviewServer(
        lambda value: preview,
        listener_factory=lambda *args, **kwargs: ServerListener(rejected),
        wallet_pid=lambda: 202, peer_pid=lambda handle: 303,
        process_image=lambda pid: tmp_path / "Other.exe",
        expected_guard_path=expected,
    )
    with pytest.raises(ControlProtocolError):
        server.serve_once()
    assert rejected.sent == []
