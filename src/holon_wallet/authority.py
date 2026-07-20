"""In-memory single-action authorization for the M3.03 simulation."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum

from .model import ProfileSummary

ACTION_LIFETIME = timedelta(minutes=5)
SIMULATED_RECIPIENT = "Simulation recipient · not a real address"


class WalletAuthorityState(str, Enum):
    LOCKED = "LOCKED"
    PREPARED = "PREPARED"
    AUTHORIZED = "AUTHORIZED"


class ActionOutcome(str, Enum):
    AUTHORIZED = "AUTHORIZED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    INVALIDATED = "INVALIDATED"
    FAILED = "FAILED"
    REPLAYED = "REPLAYED"


class ActionStateError(RuntimeError):
    """The requested transition is not valid for the current action state."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_action_id() -> str:
    return f"act-{uuid.uuid4()}"


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True, slots=True)
class PreparedMockAction:
    action_id: str
    profile_id: str
    account_label: str
    sender: str
    network: str
    chain_id: int
    token: str
    amount_atomic: int
    amount_display: str
    recipient: str
    fee_status: str
    simulation: bool
    created_at: datetime
    expires_at: datetime

    def material_fields(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "profile_id": self.profile_id,
            "account_label": self.account_label,
            "sender": self.sender,
            "network": self.network,
            "chain_id": self.chain_id,
            "token": self.token,
            "amount_atomic": self.amount_atomic,
            "amount_display": self.amount_display,
            "recipient": self.recipient,
            "fee_status": self.fee_status,
            "simulation": self.simulation,
            "created_at": _timestamp(self.created_at),
            "expires_at": _timestamp(self.expires_at),
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.material_fields(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


class CriticalActionCoordinator:
    """Owns one transient action and never exposes reusable authorization."""

    def __init__(
        self,
        clock: Callable[[], datetime] = _utc_now,
        action_id_factory: Callable[[], str] = _new_action_id,
    ) -> None:
        self._clock = clock
        self._action_id_factory = action_id_factory
        self._state = WalletAuthorityState.LOCKED
        self._current: PreparedMockAction | None = None
        self._terminal_ids: set[str] = set()

    @property
    def state(self) -> WalletAuthorityState:
        return self._state

    @property
    def current(self) -> PreparedMockAction | None:
        return self._current

    def prepare(self, profile: ProfileSummary) -> PreparedMockAction:
        if self._state is not WalletAuthorityState.LOCKED or self._current is not None:
            raise ActionStateError("A critical action is already active")
        action_id = self._action_id_factory()
        if action_id in self._terminal_ids:
            raise ActionStateError("Terminal action IDs cannot be reused")
        created_at = self._clock().astimezone(UTC)
        action = PreparedMockAction(
            action_id=action_id,
            profile_id=profile.profile_id,
            account_label=profile.label,
            sender=profile.address,
            network="Base",
            chain_id=8453,
            token="USDC",
            amount_atomic=1_000_000,
            amount_display="1 USDC",
            recipient=SIMULATED_RECIPIENT,
            fee_status="Unavailable · no RPC request",
            simulation=True,
            created_at=created_at,
            expires_at=created_at + ACTION_LIFETIME,
        )
        self._current = action
        self._state = WalletAuthorityState.PREPARED
        return action

    def preflight(self, action_id: str, digest: str) -> ActionOutcome | None:
        current = self._current
        if current is None:
            return (
                ActionOutcome.REPLAYED
                if action_id in self._terminal_ids
                else ActionOutcome.INVALIDATED
            )
        if current.action_id != action_id or current.digest != digest:
            self._terminalize(ActionOutcome.INVALIDATED)
            return ActionOutcome.INVALIDATED
        if self._clock().astimezone(UTC) >= current.expires_at:
            self._terminalize(ActionOutcome.EXPIRED)
            return ActionOutcome.EXPIRED
        return None

    def authentication_failed(self, action_id: str) -> ActionOutcome:
        if self._current is None or self._current.action_id != action_id:
            return ActionOutcome.REPLAYED
        return self._terminalize(ActionOutcome.AUTHENTICATION_FAILED)

    def authorize_and_consume(
        self,
        action_id: str,
        digest: str,
        consumer: Callable[[PreparedMockAction], None],
    ) -> ActionOutcome:
        invalid = self.preflight(action_id, digest)
        if invalid is not None:
            return invalid
        current = self._current
        if current is None:
            return ActionOutcome.INVALIDATED
        self._state = WalletAuthorityState.AUTHORIZED
        try:
            consumer(current)
        except Exception:
            return self._terminalize(ActionOutcome.FAILED)
        return self._terminalize(ActionOutcome.AUTHORIZED)

    def reject(self) -> ActionOutcome:
        return self._terminalize(ActionOutcome.REJECTED)

    def cancel(self) -> ActionOutcome:
        return self._terminalize(ActionOutcome.CANCELLED)

    def fail(self) -> ActionOutcome:
        return self._terminalize(ActionOutcome.FAILED)

    def profile_changed(self, profile_id: str) -> bool:
        if self._current is None or self._current.profile_id == profile_id:
            return False
        self._terminalize(ActionOutcome.INVALIDATED)
        return True

    def close(self) -> None:
        if self._current is not None:
            self._terminalize(ActionOutcome.CANCELLED)
        else:
            self._state = WalletAuthorityState.LOCKED

    def _terminalize(self, outcome: ActionOutcome) -> ActionOutcome:
        if self._current is not None:
            self._terminal_ids.add(self._current.action_id)
        self._current = None
        self._state = WalletAuthorityState.LOCKED
        return outcome
