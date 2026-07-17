"""Plugin-side Guard boundary; the real Guard adapter arrives in M2.02."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol


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


class GuardClient(Protocol):
    def probe(self) -> GuardHealth: ...


class GuardLauncher(Protocol):
    def start(self) -> None: ...


class UnavailableGuardClient:
    def probe(self) -> GuardHealth:
        return GuardHealth.unavailable()


class DisabledGuardLauncher:
    def start(self) -> None:
        raise RuntimeError("Guard implementation is not installed")


class GuardConnector:
    """Probe, optionally launch once, then probe once more."""

    def __init__(self, client: GuardClient, launcher: GuardLauncher) -> None:
        self._client = client
        self._launcher = launcher

    @staticmethod
    def _normalize(result: object) -> GuardHealth:
        if not isinstance(result, GuardHealth):
            return GuardHealth.uncertain()
        if result.availability is GuardAvailability.AVAILABLE:
            if result.state is GuardState.UNKNOWN:
                return GuardHealth.uncertain()
            return GuardHealth.available(result.state)
        if result.availability is GuardAvailability.UNAVAILABLE:
            return GuardHealth.unavailable()
        if result.availability is GuardAvailability.UNCERTAIN:
            return GuardHealth.uncertain()
        return GuardHealth.uncertain()

    def probe(self) -> GuardHealth:
        try:
            result = self._client.probe()
        except Exception:
            return GuardHealth.uncertain()
        return self._normalize(result)

    def ensure_available(self) -> GuardHealth:
        first = self.probe()
        if first.availability is not GuardAvailability.UNAVAILABLE:
            return first
        try:
            self._launcher.start()
        except Exception:
            return GuardHealth.unavailable()
        return self.probe()
