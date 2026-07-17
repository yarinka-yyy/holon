"""Fail-closed reconstruction of Guard state at process startup."""

from __future__ import annotations

import time

from holon_guard_ipc import GuardState

from .model import GuardSnapshot
from .store import InvalidSnapshot, MissingSnapshot, SnapshotStore

RESTART_STATES = frozenset(
    {GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING}
)


def idle_snapshot(
    state: GuardState, reason: str, now: float | None = None
) -> GuardSnapshot:
    timestamp = time.time() if now is None else now
    return GuardSnapshot(state, None, None, None, reason, timestamp)


def best_effort_save(store: SnapshotStore, snapshot: GuardSnapshot) -> None:
    try:
        store.save(snapshot)
    except OSError:
        return


def restore_snapshot(store: SnapshotStore) -> GuardSnapshot:
    try:
        snapshot = store.load()
    except MissingSnapshot:
        snapshot = idle_snapshot(GuardState.SIGNING_DISABLED, "STATE_MISSING")
        best_effort_save(store, snapshot)
    except InvalidSnapshot:
        snapshot = idle_snapshot(GuardState.SIGNING_DISABLED, "STATE_INVALID")
        best_effort_save(store, snapshot)
    else:
        if snapshot.state in RESTART_STATES:
            snapshot = GuardSnapshot(
                GuardState.RECOVERY_REQUIRED,
                snapshot.flow_id,
                None,
                None,
                "GUARD_RESTARTED",
                time.time(),
                snapshot.action_id,
                snapshot.action_fingerprint,
            )
            best_effort_save(store, snapshot)
    return snapshot
