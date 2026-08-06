"""Atomic public cache for the last confirmed state of active Earn providers."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from dataclasses import replace
from pathlib import Path
from typing import Mapping

from .contracts import (
    AvailabilityState,
    EarnContractError,
    EarnProviderResult,
    FreshnessState,
    ProviderSource,
    ProviderState,
    _timestamp,
    validate_account,
)

SNAPSHOT_SCHEMA_VERSION = "1"
MAX_CACHE_BYTES = 512 * 1024
MAX_ACCOUNTS = 20
MAX_PROVIDERS = 32


class EarnSnapshotStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._write_blocked = False

    def load(
        self, account: Mapping[str, str] | None, provider_id: str,
        active_provider_ids: frozenset[str],
    ) -> EarnProviderResult | None:
        normalized = validate_account(account)
        if normalized is None or provider_id not in active_provider_ids:
            return None
        with self._lock:
            try:
                envelope = self._load_envelope()
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, EarnContractError):
                self._write_blocked = self.path.exists()
                return None
        account_entry = next(
            (item for item in envelope["accounts"] if item["address"] == normalized["address"]),
            None,
        )
        provider_entry = next(
            (
                item for item in account_entry["providers"]
                if item["provider_id"] == provider_id
            ),
            None,
        ) if account_entry is not None else None
        if provider_entry is None:
            return None
        result = EarnProviderResult.from_dict(provider_entry["result"])
        products = tuple(
            replace(
                product,
                freshness=(
                    FreshnessState.CACHED
                    if product.position.availability is AvailabilityState.AVAILABLE
                    else FreshnessState.UNAVAILABLE
                ),
            )
            for product in result.products
        )
        return EarnProviderResult(
            result.provider_id, result.category, result.network_ids,
            ProviderState.DEGRADED, ProviderSource.CACHED, products,
            result.observed_at, "EARN_PROVIDER_CACHED",
            "Provider is unavailable; the last confirmed public snapshot is shown.",
        )

    def save(
        self, account: Mapping[str, str] | None, result: EarnProviderResult,
        active_provider_ids: frozenset[str], saved_at: str,
    ) -> bool:
        normalized = validate_account(account)
        if (
            normalized is None
            or self._write_blocked
            or result.provider_id not in active_provider_ids
            or result.source is not ProviderSource.LIVE
            or not any(
                product.position.availability is AvailabilityState.AVAILABLE
                for product in result.products
            )
        ):
            return False
        with self._lock:
            try:
                envelope = self._load_envelope()
            except (OSError, UnicodeError, ValueError, TypeError, json.JSONDecodeError, EarnContractError):
                if self.path.exists():
                    self._write_blocked = True
                    return False
                envelope = {"accounts": [], "schema_version": SNAPSHOT_SCHEMA_VERSION}
            accounts = [
                item for item in envelope["accounts"]
                if item["address"] != normalized["address"]
            ][-(MAX_ACCOUNTS - 1):]
            previous = next(
                (item for item in envelope["accounts"] if item["address"] == normalized["address"]),
                {"address": normalized["address"], "providers": []},
            )
            providers = [
                item for item in previous["providers"]
                if item["provider_id"] in active_provider_ids
                and item["provider_id"] != result.provider_id
            ][-(MAX_PROVIDERS - 1):]
            providers.append({
                "provider_id": result.provider_id,
                "result": result.to_dict(),
                "saved_at": _timestamp(saved_at),
            })
            providers.sort(key=lambda item: item["provider_id"])
            accounts.append({"address": normalized["address"], "providers": providers})
            accounts.sort(key=lambda item: item["address"])
            self._atomic_write({
                "accounts": accounts,
                "schema_version": SNAPSHOT_SCHEMA_VERSION,
            })
            return True

    def _load_envelope(self) -> dict[str, object]:
        if not self.path.exists():
            return {"accounts": [], "schema_version": SNAPSHOT_SCHEMA_VERSION}
        if self.path.stat().st_size > MAX_CACHE_BYTES:
            raise ValueError("Earn cache is too large")
        with self.path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        if (
            not isinstance(value, dict)
            or set(value) != {"accounts", "schema_version"}
            or value["schema_version"] != SNAPSHOT_SCHEMA_VERSION
            or not isinstance(value["accounts"], list)
            or len(value["accounts"]) > MAX_ACCOUNTS
        ):
            raise ValueError("Earn cache envelope is invalid")
        addresses: set[str] = set()
        for account in value["accounts"]:
            if (
                not isinstance(account, dict)
                or set(account) != {"address", "providers"}
                or not isinstance(account["providers"], list)
                or len(account["providers"]) > MAX_PROVIDERS
            ):
                raise ValueError("Earn cache account is invalid")
            normalized = validate_account({"address": account["address"], "label": "Cache"})
            assert normalized is not None
            if normalized["address"] in addresses:
                raise ValueError("Duplicate Earn cache account")
            addresses.add(normalized["address"])
            provider_ids: set[str] = set()
            for provider in account["providers"]:
                if (
                    not isinstance(provider, dict)
                    or set(provider) != {"provider_id", "result", "saved_at"}
                    or not isinstance(provider["result"], dict)
                ):
                    raise ValueError("Earn cache provider is invalid")
                result = EarnProviderResult.from_dict(provider["result"])
                _timestamp(provider["saved_at"])
                if (
                    result.provider_id != provider["provider_id"]
                    or result.provider_id in provider_ids
                    or result.source is not ProviderSource.LIVE
                    or not any(
                        product.position.availability is AvailabilityState.AVAILABLE
                        for product in result.products
                    )
                ):
                    raise ValueError("Duplicate Earn cache provider")
                provider_ids.add(result.provider_id)
        return value

    def _atomic_write(self, value: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = -1
                json.dump(value, stream, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            if temporary.stat().st_size > MAX_CACHE_BYTES:
                raise OSError("Earn cache exceeds its bound")
            os.replace(temporary, self.path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)
