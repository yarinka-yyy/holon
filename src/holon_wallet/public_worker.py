"""One-shot headless public balance reader owned by the Wallet process."""

from __future__ import annotations

import os
import threading
import time

from holon_wallet_control import WalletPublicServer

from .model import ProfileSummary, WalletShellState
from .public_data import (
    NETWORKS,
    NetworkSnapshot,
    PublicDataService,
    PublicDataStatus,
)
from .settings import SettingsStore
from .storage import StorageError, WalletPaths
from .vault import VaultRepository, VaultUnavailableError

READ_DEADLINE_SECONDS = 20.0
WORKER_WATCHDOG_SECONDS = 35.0


def _active_profile(
    repository: VaultRepository, settings: SettingsStore,
) -> ProfileSummary:
    profiles = repository.load_public()
    valid_ids = {profile.profile_id for profile in profiles}
    state = WalletShellState(profiles, settings.load_active_id(valid_ids))
    active = state.active_profile
    if active is None:
        raise VaultUnavailableError("Wallet vault is unavailable")
    return active


def _asset_payload(spec, balance, error_code: str | None) -> dict[str, object]:
    if balance is None:
        return {
            "asset_id": spec.asset_id,
            "asset": spec.symbol,
            "status": "UNAVAILABLE",
            "amount_atomic": None,
            "decimals": spec.decimals,
            "display": None,
            "error_code": error_code or "DATA_UNAVAILABLE",
        }
    return {
        "asset_id": spec.asset_id,
        "asset": balance.symbol,
        "status": "LIVE",
        "amount_atomic": str(balance.atomic_units),
        "decimals": balance.decimals,
        "display": balance.display_value,
        "error_code": None,
    }


def _network_payload(snapshot: NetworkSnapshot) -> dict[str, object]:
    spec = next(item for item in NETWORKS if item.network_id == snapshot.network_id)
    by_id = snapshot.assets_by_id
    errors = snapshot.errors_by_id
    balances = [
        _asset_payload(asset, by_id.get(asset.asset_id), errors.get(asset.asset_id))
        for asset in spec.assets
    ]
    if snapshot.status is PublicDataStatus.UNAVAILABLE:
        return {
            "network": snapshot.network_id,
            "chain_id": snapshot.chain_id,
            "status": "UNAVAILABLE",
            "block_number": None,
            "updated_at": None,
            "error_code": snapshot.error_code or "DATA_UNAVAILABLE",
            "balances": balances,
        }
    partial = any(item["status"] == "UNAVAILABLE" for item in balances)
    return {
        "network": snapshot.network_id,
        "chain_id": snapshot.chain_id,
        "status": "PARTIAL" if partial else snapshot.status.value,
        "block_number": str(snapshot.block_number),
        "updated_at": snapshot.updated_at,
        "error_code": "ASSET_DATA_UNAVAILABLE" if partial else snapshot.error_code,
        "balances": balances,
    }


def _empty_networks(error_code: str) -> list[dict[str, object]]:
    return [
        _network_payload(NetworkSnapshot.unavailable(spec, error_code))
        for spec in NETWORKS
    ]


def _degraded(code: str, message: str, error_code: str) -> dict[str, object]:
    return {
        "balance_schema_version": "2",
        "status": "DEGRADED",
        "authority_available": False,
        "account": None,
        "networks": _empty_networks(error_code),
        "code": code,
        "message": message,
    }


def _read_networks(
    service: PublicDataService, active: ProfileSummary,
) -> tuple[NetworkSnapshot, ...]:
    results: dict[str, NetworkSnapshot] = {}
    lock = threading.Lock()

    def read(network_id: str) -> None:
        try:
            result = service.refresh(
                active.profile_id, active.address, (network_id,),
            ).networks[0]
        except Exception:
            spec = next(item for item in NETWORKS if item.network_id == network_id)
            result = NetworkSnapshot.unavailable(spec, "RPC_UNAVAILABLE")
        with lock:
            results[network_id] = result

    threads = [
        threading.Thread(target=read, args=(spec.network_id,), daemon=True)
        for spec in NETWORKS
    ]
    for thread in threads:
        thread.start()
    deadline = time.monotonic() + READ_DEADLINE_SECONDS
    for thread in threads:
        thread.join(max(0.0, deadline - time.monotonic()))
    return tuple(
        results.get(spec.network_id, NetworkSnapshot.unavailable(spec, "RPC_TIMEOUT"))
        for spec in NETWORKS
    )


def read_active_balances(
    repository: VaultRepository,
    settings: SettingsStore,
    service: PublicDataService,
) -> dict[str, object]:
    if not repository.exists:
        return _degraded(
            "WALLET_NOT_CREATED", "Wallet has not been created.", "WALLET_NOT_CREATED",
        )
    try:
        active = _active_profile(repository, settings)
    except (VaultUnavailableError, StorageError, ValueError):
        return _degraded(
            "WALLET_UNAVAILABLE", "Wallet public data is unavailable.",
            "WALLET_UNAVAILABLE",
        )
    networks = _read_networks(service, active)
    try:
        current = _active_profile(repository, settings)
    except (VaultUnavailableError, StorageError, ValueError):
        current = None
    if current != active:
        return _degraded(
            "BALANCES_UNAVAILABLE", "Wallet balances are unavailable.",
            "ACCOUNT_CHANGED",
        )
    network_payloads = [_network_payload(snapshot) for snapshot in networks]
    available = sum(item["status"] in {"LIVE", "PARTIAL"} for item in network_payloads)
    status, code, message = (
        ("READY", "BALANCES_READY", "Wallet balances are available.")
        if all(item["status"] == "LIVE" for item in network_payloads) else
        ("PARTIAL", "BALANCES_PARTIAL", "Some Wallet balances are unavailable.")
        if available else
        ("DEGRADED", "BALANCES_UNAVAILABLE", "Wallet balances are unavailable.")
    )
    return {
        "balance_schema_version": "2",
        "status": status,
        "authority_available": False,
        "account": {"label": active.label, "address": active.address},
        "networks": network_payloads,
        "code": code,
        "message": message,
    }


def run_public_balances_worker(
    *,
    paths: WalletPaths | None = None,
    service: PublicDataService | None = None,
    server_factory=WalletPublicServer,
) -> int:
    wallet_paths = paths or WalletPaths.default()
    repository = VaultRepository(wallet_paths)
    settings = SettingsStore(wallet_paths)
    reader = lambda: read_active_balances(
        repository, settings, service or PublicDataService(),
    )
    watchdog = threading.Timer(WORKER_WATCHDOG_SECONDS, lambda: os._exit(2))
    watchdog.daemon = True
    watchdog.start()
    try:
        server_factory(reader).serve_once()
        return 0
    except Exception:
        return 2
    finally:
        watchdog.cancel()
