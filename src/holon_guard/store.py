"""Atomic, secret-free Guard snapshot persistence."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path

from holon_guard_ipc import GuardState

from .model import GuardSnapshot, SnapshotError

MAX_SNAPSHOT_BYTES = 16 * 1024


class MissingSnapshot(FileNotFoundError):
    pass


class InvalidSnapshot(ValueError):
    pass


class SnapshotStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> GuardSnapshot:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError as exc:
            raise MissingSnapshot("Guard snapshot is missing") from exc
        except OSError as exc:
            raise InvalidSnapshot("Guard snapshot is unreadable") from exc
        if len(raw) > MAX_SNAPSHOT_BYTES:
            raise InvalidSnapshot("Guard snapshot is oversized")
        try:
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict):
                raise SnapshotError("Guard snapshot must be an object")
            return GuardSnapshot.from_dict(value)
        except (UnicodeDecodeError, json.JSONDecodeError, SnapshotError) as exc:
            raise InvalidSnapshot("Guard snapshot is invalid") from exc

    def save(self, snapshot: GuardSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            snapshot.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="\n",
                dir=self.path.parent,
                prefix=".guard-state-",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def bootstrap_normal_for_test(self, now: float | None = None) -> GuardSnapshot:
        timestamp = time.time() if now is None else now
        snapshot = GuardSnapshot(
            GuardState.NORMAL, None, None, None, "TEST_BOOTSTRAP", timestamp
        )
        self.save(snapshot)
        return snapshot
