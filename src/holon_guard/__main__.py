"""Standalone Guard entry point. Packaging wiring remains M2.05 scope."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from holon_guard_ipc import PIPE_NAME

from .lifecycle import GuardLifecycle
from .lock import GuardAlreadyRunning, SingleInstanceLock
from .server import GuardServer
from .store import SnapshotStore
from .wallet import UnavailableWalletController, WindowsOwnerProbe


def _default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return Path(local_app_data) / "Holon" / "data"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="holon-guard")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--pipe-name", default=PIPE_NAME)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir or _default_data_dir()
    try:
        with SingleInstanceLock(data_dir / "guard.lock"):
            store = SnapshotStore(data_dir / "guard-state.json")
            lifecycle = GuardLifecycle.restore(
                store, UnavailableWalletController(), WindowsOwnerProbe()
            )
            GuardServer(args.pipe_name, lifecycle).serve_forever()
    except GuardAlreadyRunning:
        return 3
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
