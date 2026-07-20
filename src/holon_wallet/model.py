"""Public-only in-memory state for the M3.01 Wallet shell."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ProfileSummary:
    profile_id: str
    label: str
    address: str
    simulated: bool = True

    @property
    def short_address(self) -> str:
        return f"{self.address[:6]}...{self.address[-5:]}"


PROTOTYPE_PROFILES = (
    ProfileSummary(
        "main", "Main Account", "0x1111111111111111111111111111111111111111",
    ),
    ProfileSummary(
        "trading", "Trading Account", "0x2222222222222222222222222222222222222222",
    ),
    ProfileSummary(
        "savings", "Savings Account", "0x3333333333333333333333333333333333333333",
    ),
)


class WalletShellState:
    """Keeps prototype profile selection in memory for one process lifetime."""

    def __init__(self, profiles: tuple[ProfileSummary, ...] = PROTOTYPE_PROFILES) -> None:
        if not profiles:
            raise ValueError("At least one prototype profile is required")
        if len({profile.profile_id for profile in profiles}) != len(profiles):
            raise ValueError("Prototype profile IDs must be unique")
        self._profiles = profiles
        self._active_profile_id = profiles[0].profile_id

    @property
    def profiles(self) -> tuple[ProfileSummary, ...]:
        return self._profiles

    @property
    def active_profile_id(self) -> str:
        return self._active_profile_id

    @property
    def active_profile(self) -> ProfileSummary:
        return next(
            profile for profile in self._profiles
            if profile.profile_id == self._active_profile_id
        )

    def select_profile(self, profile_id: str) -> bool:
        if not any(profile.profile_id == profile_id for profile in self._profiles):
            return False
        self._active_profile_id = profile_id
        return True
