"""Atomic bounded persistence for request-control state."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from .request_model import RequestControlSnapshot, RequestStateError

MAX_REQUEST_STATE_BYTES = 128 * 1024


class MissingRequestState(FileNotFoundError):
    pass


class InvalidRequestState(ValueError):
    pass


class RequestStateStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> RequestControlSnapshot:
        try:
            raw = self.path.read_bytes()
        except FileNotFoundError as exc:
            raise MissingRequestState("Request-control state is missing") from exc
        except OSError as exc:
            raise InvalidRequestState("Request-control state is unreadable") from exc
        if len(raw) > MAX_REQUEST_STATE_BYTES:
            raise InvalidRequestState("Request-control state is oversized")
        try:
            value = json.loads(raw.decode("utf-8"))
            return RequestControlSnapshot.from_dict(value)
        except (UnicodeDecodeError, json.JSONDecodeError, RequestStateError) as exc:
            raise InvalidRequestState("Request-control state is invalid") from exc

    def save(self, snapshot: RequestControlSnapshot) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            snapshot.to_dict(), ensure_ascii=False, separators=(",", ":"), sort_keys=True
        )
        temporary_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n", dir=self.path.parent,
                prefix=".request-control-", suffix=".tmp", delete=False,
            ) as temporary:
                temporary.write(payload)
                temporary.flush()
                os.fsync(temporary.fileno())
                temporary_path = Path(temporary.name)
            os.replace(temporary_path, self.path)
        finally:
            if temporary_path is not None and temporary_path.exists():
                temporary_path.unlink()

    def bootstrap_empty_for_test(self) -> RequestControlSnapshot:
        snapshot = RequestControlSnapshot((), None, None)
        self.save(snapshot)
        return snapshot
