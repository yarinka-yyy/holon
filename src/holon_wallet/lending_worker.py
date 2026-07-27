"""Headless Wallet-owned Lending preview worker with no authentication."""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Mapping

from holon_lending import (
    ActionProfilesState,
    LendingPreflightError,
    LendingPreflightService,
)
from holon_lending.preflight import unavailable_preview
from holon_wallet_control import WalletLendingPreviewServer

from .public_worker import _active_profile
from .settings import SettingsStore
from .storage import StorageError, WalletPaths
from .vault import VaultRepository, VaultUnavailableError

WORKER_WATCHDOG_SECONDS = 35.0


def read_lending_preview(
    request: Mapping[str, object], repository: VaultRepository,
    settings: SettingsStore, service: LendingPreflightService,
) -> dict[str, object]:
    intent = request.get("intent")
    if not isinstance(intent, dict):
        return unavailable_preview("LENDING_ACTION_INVALID")
    requested = intent.get("action") if intent.get("action") in {"supply", "withdraw"} else None
    mode = intent.get("amount_mode") if intent.get("amount_mode") in {"exact", "all"} else None
    digest = request.get("profile_digest")
    profile_digest = digest if isinstance(digest, str) else None
    profile_id = (
        str(intent.get("protocol_profile_id"))
        if intent.get("protocol_profile_id") in {
            "aave-v3-base-usdc", "compound-v3-base-usdc",
            "morpho-v1-gauntlet-usdc-prime",
        } else "aave-v3-base-usdc"
    )
    if not repository.exists:
        return unavailable_preview(
            "WALLET_ACCOUNT_UNAVAILABLE", requested_action=requested,
            amount_mode=mode, profile_digest=profile_digest,
            profile_id=profile_id,
        )
    try:
        active = _active_profile(repository, settings)
    except (VaultUnavailableError, StorageError, ValueError):
        return unavailable_preview(
            "WALLET_ACCOUNT_UNAVAILABLE", requested_action=requested,
            amount_mode=mode, profile_digest=profile_digest,
            profile_id=profile_id,
        )
    account = {"label": active.label, "address": active.address}
    try:
        preview = service.prepare(
            intent, account, expected_profile_digest=profile_digest or "",
        )
    except LendingPreflightError as exc:
        preview = unavailable_preview(
            exc.code, requested_action=requested, amount_mode=mode,
            account=account, profile_digest=profile_digest,
            profile_id=profile_id,
        )
    try:
        current = _active_profile(repository, settings)
    except (VaultUnavailableError, StorageError, ValueError):
        current = None
    if current != active:
        return unavailable_preview(
            "ACCOUNT_CHANGED", requested_action=requested, amount_mode=mode,
            profile_digest=profile_digest,
            profile_id=profile_id,
        )
    return preview


def run_lending_preview_worker(
    *, paths: WalletPaths | None = None,
    service_factory: Callable[[], LendingPreflightService] | None = None,
    server_factory=WalletLendingPreviewServer,
) -> int:
    wallet_paths = paths or WalletPaths.default()
    repository = VaultRepository(wallet_paths)
    settings = SettingsStore(wallet_paths)
    factory = service_factory or (
        lambda: LendingPreflightService(ActionProfilesState.load())
    )
    handler = lambda request: read_lending_preview(
        request, repository, settings, factory(),
    )
    watchdog = threading.Timer(WORKER_WATCHDOG_SECONDS, lambda: os._exit(2))
    watchdog.daemon = True
    watchdog.start()
    try:
        server_factory(handler).serve_once()
        return 0
    except Exception:
        return 2
    finally:
        watchdog.cancel()
