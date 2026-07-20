"""Public-only active Account selection persistence."""

from __future__ import annotations

from .storage import StorageError, WalletPaths, atomic_write_json, read_json

SETTINGS_VERSION = 1


class SettingsStore:
    def __init__(self, paths: WalletPaths) -> None:
        self.paths = paths

    def load_active_id(self, valid_ids: set[str]) -> str | None:
        if not self.paths.settings.exists():
            return None
        try:
            value = read_json(self.paths.settings)
        except StorageError:
            return None
        if not isinstance(value, dict) or set(value) != {"schema_version", "active_profile_id"}:
            return None
        profile_id = value.get("active_profile_id")
        if value.get("schema_version") != SETTINGS_VERSION or profile_id not in valid_ids:
            return None
        return profile_id

    def save_active_id(self, profile_id: str) -> None:
        atomic_write_json(
            self.paths.settings,
            {"schema_version": SETTINGS_VERSION, "active_profile_id": profile_id},
        )
