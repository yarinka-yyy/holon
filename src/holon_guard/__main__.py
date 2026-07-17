"""Standalone Guard entry point. Packaging wiring remains M2.05 scope."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from holon_guard_ipc import PIPE_NAME
from holon_contracts import RefusalCode, SecurityCode
from holon_policy import Policy, PolicyEngine, PolicyLoadError
from holon_policy.baseline import load_baseline_policy
from holon_journal import EventType

from .action_model import ActionStateSnapshot
from .action_store import ActionStateStore, InvalidActionState, MissingActionState
from .actions import ActionLedger
from .authority import AuthorityService
from .lifecycle import GuardLifecycle
from .lock import GuardAlreadyRunning, SingleInstanceLock
from .runtime_security import load_authority_audit
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
            action_store = ActionStateStore(data_dir / "action-state.json")
            action_failure: str | None = None
            try:
                action_snapshot = action_store.load()
            except (MissingActionState, InvalidActionState):
                action_snapshot = ActionStateSnapshot(None, ())
                action_failure = SecurityCode.ACTION_STATE_INVALID.value
            policy_failure: str | None = None
            try:
                policy = load_baseline_policy()
            except PolicyLoadError as exc:
                policy = Policy("1", "1", False, ())
                policy_failure = exc.code
            ledger = ActionLedger(action_store, action_snapshot)
            lifecycle = GuardLifecycle.restore(
                store, UnavailableWalletController(), WindowsOwnerProbe(), ledger
            )
            audit, audit_failure = load_authority_audit(data_dir)
            failure = audit_failure or policy_failure or action_failure
            if failure is not None:
                lifecycle.disable_signing(failure)
            elif not policy.authority_enabled:
                lifecycle.disable_signing(RefusalCode.POLICY_AUTHORITY_DISABLED.value)
            authority = AuthorityService(
                lifecycle, PolicyEngine(policy), audit, security_failure=failure
            )
            if lifecycle.snapshot.state.value == "SIGNING_DISABLED":
                authority.audit_system(
                    EventType.SIGNING_DISABLED, lifecycle.snapshot.reason,
                    guard_state=lifecycle.snapshot.state.value,
                )
            GuardServer(args.pipe_name, authority).serve_forever()
    except GuardAlreadyRunning:
        return 3
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
