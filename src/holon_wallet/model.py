"""Public Wallet profile state shared by the vault and QML controller."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    profile_id: str
    label: str
    address: str
    profile_type: str
    derivation_path: str | None
    created_at: str

    @property
    def short_address(self) -> str:
        return f"{self.address[:6]}...{self.address[-5:]}"


class WalletShellState:
    """Keeps locked public summaries and the selected public profile."""

    def __init__(
        self,
        profiles: tuple[ProfileSummary, ...] = (),
        active_profile_id: str | None = None,
    ) -> None:
        if len({profile.profile_id for profile in profiles}) != len(profiles):
            raise ValueError("Profile IDs must be unique")
        self._profiles = profiles
        ids = {profile.profile_id for profile in profiles}
        self._active_profile_id = (
            active_profile_id if active_profile_id in ids
            else profiles[0].profile_id if profiles else None
        )

    @property
    def profiles(self) -> tuple[ProfileSummary, ...]:
        return self._profiles

    @property
    def active_profile_id(self) -> str | None:
        return self._active_profile_id

    @property
    def active_profile(self) -> ProfileSummary | None:
        if self._active_profile_id is None:
            return None
        return next(
            profile for profile in self._profiles
            if profile.profile_id == self._active_profile_id
        )

    def select_profile(self, profile_id: str) -> bool:
        if not any(profile.profile_id == profile_id for profile in self._profiles):
            return False
        self._active_profile_id = profile_id
        return True

    def replace_profiles(
        self,
        profiles: tuple[ProfileSummary, ...],
        active_profile_id: str | None = None,
    ) -> None:
        if not profiles:
            raise ValueError("At least one profile is required")
        if len({profile.profile_id for profile in profiles}) != len(profiles):
            raise ValueError("Profile IDs must be unique")
        self._profiles = profiles
        ids = {profile.profile_id for profile in profiles}
        self._active_profile_id = (
            active_profile_id if active_profile_id in ids else profiles[0].profile_id
        )
