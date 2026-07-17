"""Persistent Guard state and safe internal results."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from holon_guard_ipc import GuardState

STATE_VERSION = 1
SNAPSHOT_FIELDS = frozenset(
    {"state_version", "state", "flow_id", "owner_pid", "wallet_pid", "reason", "updated_at"}
)


class SnapshotError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class GuardSnapshot:
    state: GuardState
    flow_id: str | None
    owner_pid: int | None
    wallet_pid: int | None
    reason: str
    updated_at: float
    state_version: int = STATE_VERSION

    def to_dict(self) -> dict[str, Any]:
        return {
            "state_version": self.state_version,
            "state": self.state.value,
            "flow_id": self.flow_id,
            "owner_pid": self.owner_pid,
            "wallet_pid": self.wallet_pid,
            "reason": self.reason,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "GuardSnapshot":
        if set(value) != SNAPSHOT_FIELDS or value.get("state_version") != STATE_VERSION:
            raise SnapshotError("Invalid Guard snapshot envelope")
        try:
            state = GuardState(value.get("state"))
        except (TypeError, ValueError) as exc:
            raise SnapshotError("Invalid Guard state") from exc
        flow_id = value.get("flow_id")
        owner_pid = value.get("owner_pid")
        wallet_pid = value.get("wallet_pid")
        reason = value.get("reason")
        updated_at = value.get("updated_at")
        if state is GuardState.UNKNOWN:
            raise SnapshotError("Unknown state cannot be persisted")
        if flow_id is not None and (not isinstance(flow_id, str) or not flow_id or len(flow_id) > 64):
            raise SnapshotError("Invalid persisted flow ID")
        for pid in (owner_pid, wallet_pid):
            if pid is not None and (type(pid) is not int or pid <= 0):
                raise SnapshotError("Invalid persisted PID")
        if not isinstance(reason, str) or len(reason) > 64:
            raise SnapshotError("Invalid persisted reason")
        if type(updated_at) not in (int, float) or updated_at < 0:
            raise SnapshotError("Invalid persisted timestamp")
        protected = {GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING}
        if state in protected and (flow_id is None or owner_pid is None):
            raise SnapshotError("Protected state lacks flow owner")
        if state in {GuardState.ACTIVE, GuardState.EXITING} and wallet_pid is None:
            raise SnapshotError("Active state lacks Wallet PID")
        if state is GuardState.RECOVERY_REQUIRED and flow_id is None:
            raise SnapshotError("Recovery state lacks flow ID")
        if state in {GuardState.NORMAL, GuardState.SIGNING_DISABLED} and any(
            item is not None for item in (flow_id, owner_pid, wallet_pid)
        ):
            raise SnapshotError("Idle state contains active process data")
        if state is GuardState.RECOVERY_REQUIRED and any(
            item is not None for item in (owner_pid, wallet_pid)
        ):
            raise SnapshotError("Recovery state contains stale process data")
        return cls(state, flow_id, owner_pid, wallet_pid, reason, float(updated_at))


@dataclass(frozen=True, slots=True)
class GuardResult:
    ok: bool
    code: str
    state: GuardState
    message: str
    flow_id: str | None = None
