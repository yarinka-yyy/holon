"""Persistent semantic-request attempts and global temporary block."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Mapping

REQUEST_STATE_VERSION = 1
MAX_RECENT_ATTEMPTS = 256
STATE_FIELDS = frozenset(
    {"state_version", "attempts", "blocked_until", "block_fingerprint"}
)
ATTEMPT_FIELDS = frozenset({"fingerprint", "observed_at"})
FINGERPRINT_RE = re.compile(r"^[0-9a-f]{64}$")


class RequestStateError(ValueError):
    pass


def _timestamp(value: object) -> float:
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise RequestStateError("Invalid request-control timestamp")
    return float(value)


@dataclass(frozen=True, slots=True)
class RequestAttempt:
    fingerprint: str
    observed_at: float

    def to_dict(self) -> dict[str, Any]:
        return {"fingerprint": self.fingerprint, "observed_at": self.observed_at}

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RequestAttempt":
        if not isinstance(value, Mapping) or set(value) != ATTEMPT_FIELDS:
            raise RequestStateError("Invalid request attempt")
        fingerprint = value.get("fingerprint")
        if not isinstance(fingerprint, str) or FINGERPRINT_RE.fullmatch(fingerprint) is None:
            raise RequestStateError("Invalid request fingerprint")
        return cls(fingerprint, _timestamp(value.get("observed_at")))


@dataclass(frozen=True, slots=True)
class RequestControlSnapshot:
    attempts: tuple[RequestAttempt, ...]
    blocked_until: float | None
    block_fingerprint: str | None
    state_version: int = REQUEST_STATE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_version": self.state_version,
            "attempts": [attempt.to_dict() for attempt in self.attempts],
            "blocked_until": self.blocked_until,
            "block_fingerprint": self.block_fingerprint,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RequestControlSnapshot":
        if not isinstance(value, Mapping) or set(value) != STATE_FIELDS:
            raise RequestStateError("Invalid request-control fields")
        if value.get("state_version") != REQUEST_STATE_VERSION:
            raise RequestStateError("Unsupported request-control version")
        raw_attempts = value.get("attempts")
        if not isinstance(raw_attempts, list) or len(raw_attempts) > MAX_RECENT_ATTEMPTS:
            raise RequestStateError("Invalid recent request attempts")
        attempts = tuple(RequestAttempt.from_dict(item) for item in raw_attempts)
        observed = [attempt.observed_at for attempt in attempts]
        if observed != sorted(observed):
            raise RequestStateError("Request attempts are out of order")
        blocked_until = value.get("blocked_until")
        block_fingerprint = value.get("block_fingerprint")
        if (blocked_until is None) != (block_fingerprint is None):
            raise RequestStateError("Incomplete request block")
        if blocked_until is not None:
            blocked_until = _timestamp(blocked_until)
            if not isinstance(block_fingerprint, str) or FINGERPRINT_RE.fullmatch(
                block_fingerprint
            ) is None:
                raise RequestStateError("Invalid block fingerprint")
            matching = sum(item.fingerprint == block_fingerprint for item in attempts)
            if matching < 3 or (observed and blocked_until <= observed[-1]):
                raise RequestStateError("Impossible request block")
        return cls(attempts, blocked_until, block_fingerprint)
