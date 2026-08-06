"""Public-only Wallet preference persistence."""

from __future__ import annotations

from .storage import StorageError, WalletPaths, atomic_write_json, read_json

SETTINGS_VERSION = 2


class SettingsStore:
    def __init__(self, paths: WalletPaths) -> None:
        self.paths = paths

    def load_active_id(self, valid_ids: set[str]) -> str | None:
        profile_id, _ = self._load()
        return profile_id if profile_id in valid_ids else None

    def load_show_zero_balances(self) -> bool:
        _, show_zero_balances = self._load()
        return show_zero_balances

    def save_active_id(self, profile_id: str) -> None:
        _, show_zero_balances = self._load()
        self._save(profile_id, show_zero_balances)

    def save_show_zero_balances(
        self, show_zero_balances: bool, active_profile_id: str,
    ) -> None:
        if type(show_zero_balances) is not bool:
            raise ValueError("Show-zero preference must be boolean")
        self._save(active_profile_id, show_zero_balances)

    def _load(self) -> tuple[str | None, bool]:
        if not self.paths.settings.exists():
            return None, False
        try:
            value = read_json(self.paths.settings)
        except StorageError:
            return None, False
        if not isinstance(value, dict):
            return None, False
        version = value.get("schema_version")
        expected_keys = (
            {"schema_version", "active_profile_id"}
            if version == 1 else
            {"schema_version", "active_profile_id", "show_zero_balances"}
        )
        if version not in {1, SETTINGS_VERSION} or set(value) != expected_keys:
            return None, False
        profile_id = value.get("active_profile_id")
        if not isinstance(profile_id, str) or not profile_id:
            return None, False
        if version == 1:
            return profile_id, False
        show_zero_balances = value.get("show_zero_balances")
        if type(show_zero_balances) is not bool:
            return profile_id, False
        return profile_id, show_zero_balances

    def _save(self, profile_id: str, show_zero_balances: bool) -> None:
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError("Active profile ID is invalid")
        atomic_write_json(
            self.paths.settings,
            {
                "schema_version": SETTINGS_VERSION,
                "active_profile_id": profile_id,
                "show_zero_balances": show_zero_balances,
            },
        )
