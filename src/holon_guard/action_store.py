"""Atomic bounded persistence for replay-protection state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .action_model import ActionStateError, ActionStateSnapshot

MAX_ACTION_STATE_BYTES = 2 * 1024 * 1024


class MissingActionState(FileNotFoundError):
    pass


class InvalidActionState(ValueError):
    pass


class ActionStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> ActionStateSnapshot:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError as exc:
            raise MissingActionState("Action state is missing") from exc
        except OSError as exc:
            raise InvalidActionState("Action state is unreadable") from exc
        if len(raw) > MAX_ACTION_STATE_BYTES:
            raise InvalidActionState("Action state is oversized")
        try:
            value = json.loads(raw.decode("utf-8"))
            return ActionStateSnapshot.from_dict(value)
        except (UnicodeDecodeError, json.JSONDecodeError, ActionStateError) as exc:
            raise InvalidActionState("Action state is invalid") from exc

    def save(self, snapshot: ActionStateSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            snapshot.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=self.path.parent,
                prefix=".action-state-", suffix=".tmp", delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def bootstrap_empty_for_test(self) -> ActionStateSnapshot:
        snapshot = ActionStateSnapshot(None, ())
        self.save(snapshot)
        return snapshot
