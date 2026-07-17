"""Persistent global block after repeated semantic authority requests."""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from holon_contracts import SecurityCode

from .request_model import MAX_RECENT_ATTEMPTS, RequestAttempt, RequestControlSnapshot
from .request_store import RequestStateStore
from .semantic import semantic_fingerprint

DUPLICATE_WINDOW_SECONDS = 60.0
BLOCK_DURATION_SECONDS = 300.0
DUPLICATE_THRESHOLD = 3


class RequestControlFailure(RuntimeError):
    def __init__(self) -> None:
        super().__init__("Request-control state is unavailable")
        self.code = SecurityCode.REQUEST_CONTROL_STATE_INVALID.value


@dataclass(frozen=True, slots=True)
class RequestDecision:
    fingerprint: str
    blocked: bool
    triggered: bool
    expired: bool


class RequestController:
    def __init__(
        self, store: RequestStateStore, snapshot: RequestControlSnapshot,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.store = store
        self.snapshot = snapshot
        self.clock = clock

    def _now(self) -> float:
        now = self.clock()
        if type(now) not in (int, float) or not math.isfinite(now) or now < 0:
            raise RequestControlFailure()
        return float(now)

    def _save(self, snapshot: RequestControlSnapshot) -> None:
        try:
            self.store.save(snapshot)
        except OSError as exc:
            raise RequestControlFailure() from exc
        self.snapshot = snapshot

    def observe(self, payload: Mapping[str, Any]) -> RequestDecision:
        now = self._now()
        fingerprint = semantic_fingerprint(payload)
        expired = False
        if any(item.observed_at > now for item in self.snapshot.attempts):
            raise RequestControlFailure()
        if self.snapshot.blocked_until is not None:
            if now < self.snapshot.blocked_until:
                return RequestDecision(fingerprint, True, False, False)
            self._save(RequestControlSnapshot(self.snapshot.attempts, None, None))
            expired = True
        attempts = tuple(
            item for item in self.snapshot.attempts
            if item.observed_at >= now - DUPLICATE_WINDOW_SECONDS
        )
        if len(attempts) >= MAX_RECENT_ATTEMPTS:
            raise RequestControlFailure()
        attempts += (RequestAttempt(fingerprint, now),)
        equivalent = sum(item.fingerprint == fingerprint for item in attempts)
        triggered = equivalent >= DUPLICATE_THRESHOLD
        blocked_until = now + BLOCK_DURATION_SECONDS if triggered else None
        block_fingerprint = fingerprint if triggered else None
        self._save(RequestControlSnapshot(attempts, blocked_until, block_fingerprint))
        return RequestDecision(fingerprint, triggered, triggered, expired)

    def clear_from_wallet(self) -> bool:
        now = self._now()
        if any(item.observed_at > now for item in self.snapshot.attempts):
            raise RequestControlFailure()
        was_blocked = self.snapshot.blocked_until is not None
        self._save(RequestControlSnapshot((), None, None))
        return was_blocked


class WalletRequestControl:
    """Not exposed through Hermes IPC; future Wallet receives this narrow seam."""

    def __init__(
        self, controller: RequestController, on_clear: Callable[[], None] | None = None
    ) -> None:
        self._controller = controller
        self._on_clear = on_clear

    def clear_block(self) -> bool:
        cleared = self._controller.clear_from_wallet()
        if cleared and self._on_clear is not None:
            self._on_clear()
        return cleared
