"""Safe state types shared by the Hermes plugin and local Guard."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class GuardAvailability(str, Enum):
    AVAILABLE = "AVAILABLE"
    UNAVAILABLE = "UNAVAILABLE"
    UNCERTAIN = "UNCERTAIN"


class GuardState(str, Enum):
    NORMAL = "NORMAL"
    ENTERING = "ENTERING"
    ACTIVE = "ACTIVE"
    EXITING = "EXITING"
    RECOVERY_REQUIRED = "RECOVERY_REQUIRED"
    SIGNING_DISABLED = "SIGNING_DISABLED"
    UNKNOWN = "UNKNOWN"


PROTECTED_STATES = frozenset(
    {
        GuardState.ENTERING,
        GuardState.ACTIVE,
        GuardState.EXITING,
        GuardState.RECOVERY_REQUIRED,
    }
)


@dataclass(frozen=True, slots=True)
class GuardHealth:
    availability: GuardAvailability
    state: GuardState
    code: str
    message: str

    @classmethod
    def available(cls, state: GuardState) -> "GuardHealth":
        return cls(GuardAvailability.AVAILABLE, state, "OK", "Guard status is available.")

    @classmethod
    def unavailable(cls) -> "GuardHealth":
        return cls(
            GuardAvailability.UNAVAILABLE,
            GuardState.UNKNOWN,
            "GUARD_UNAVAILABLE",
            "Wallet authority is unavailable.",
        )

    @classmethod
    def uncertain(cls) -> "GuardHealth":
        return cls(
            GuardAvailability.UNCERTAIN,
            GuardState.UNKNOWN,
            "GUARD_STATE_UNCERTAIN",
            "Wallet authority state is uncertain.",
        )
