"""Standalone Guard entry point with optional installed-package integrity."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from holon_guard_ipc import PIPE_NAME
from holon_guard_ipc.wallet_status import STATUS_PIPE_NAME, WalletStatusServer
from holon_guard_ipc.policy_control import POLICY_CONTROL_PIPE_NAME, PolicyControlServer
from holon_contracts import RefusalCode, SecurityCode
from holon_policy import (
    Policy, PolicyEngine, PolicyLoadError, PolicyRevisionStore,
    PolicyRevisionUnavailable,
)
from holon_policy.baseline import (
    INSTALLED_POLICY_RELATIVE_PATH,
    load_baseline_policy,
)
from holon_journal import EventType
from holon_lending import (
    ActionProfilesState, LendingAnalyticsStore, LendingPortfolioService,
    LendingReadService,
)
from holon_modules import (
    CapabilityRegistry,
    default_catalog_path,
    load_registry as load_module_registry,
)
from holon_earn import (
    EarnPortfolioService,
    EarnProviderRegistry,
    EarnSnapshotStore,
    LendingEarnProvider,
    register_module_providers,
)
from holon_installation import verify_installed

from .action_model import ActionStateSnapshot
from .action_store import ActionStateStore, InvalidActionState, MissingActionState
from .actions import ActionLedger
from .authority import AuthorityService
from .lifecycle import GuardLifecycle
from .policy_control import GuardPolicyControl
from .provisioning import AuthorityStateProvisioner
from .lock import GuardAlreadyRunning, SingleInstanceLock
from holon_wallet_control.lending_operation import (
    LendingOperationSnapshot, LendingOperationStateError, LendingOperationStore,
)
from .runtime_security import load_authority_audit
from .server import GuardServer
from .store import SnapshotStore
from .wallet import (
    UnavailableWalletController,
    VerifiedWalletController,
    WalletController,
    WindowsOwnerProbe,
)
from holon_wallet.storage import WalletPaths
from holon_wallet.history import (
    HistoryStore, HistoryUnavailableError, lending_cashflows_for_address,
)
from holon_wallet.trusted_recipients import TrustedPolicyDraftStore


RECOVERABLE_SIGNING_REASONS = frozenset({
    RefusalCode.POLICY_AUTHORITY_DISABLED.value,
    "LENDING_AUTHORITY_DISABLED",
    SecurityCode.HERMES_VERSION_UNSUPPORTED.value,
})


def _default_data_dir() -> Path:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA is unavailable")
    return Path(local_app_data) / "Holon" / "data"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="holon-guard")
    parser.add_argument("--data-dir", type=Path, default=None)
    parser.add_argument("--pipe-name", default=PIPE_NAME)
    parser.add_argument("--wallet-status-pipe-name", default=STATUS_PIPE_NAME)
    parser.add_argument("--policy-control-pipe-name", default=POLICY_CONTROL_PIPE_NAME)
    parser.add_argument("--require-install-integrity", action="store_true")
    parser.add_argument("--manifest-path", type=Path, default=None)
    parser.add_argument("--app-root", type=Path, default=None)
    parser.add_argument("--plugin-root", type=Path, default=None)
    parser.add_argument("--hermes-version", default="")
    parser.add_argument("--wallet-path", type=Path, default=None)
    parser.add_argument("--module-catalog", type=Path, default=None)
    return parser


def _integrity_failure(args: argparse.Namespace) -> str | None:
    if not args.require_install_integrity:
        return None
    if not all((args.manifest_path, args.app_root, args.plugin_root)):
        return SecurityCode.PACKAGE_MANIFEST_INVALID.value
    result = verify_installed(
        args.manifest_path, args.app_root, args.plugin_root, args.hermes_version,
    )
    return None if result.ok else result.code


def _policy_path(args: argparse.Namespace) -> Path | None:
    if args.require_install_integrity and args.app_root is not None:
        return args.app_root / INSTALLED_POLICY_RELATIVE_PATH
    return None


def _any_authority_enabled(policy: Policy) -> bool:
    """Compatibility helper: schema v4 exposes protected Lending actions."""
    return (
        policy.schema_version == "4"
        or policy.authority_enabled
        or policy.lending_authority_enabled
    )


def _wallet_controller(
    args: argparse.Namespace,
    install_failure: str | None,
) -> WalletController:
    if install_failure is not None:
        return UnavailableWalletController()
    if args.require_install_integrity:
        if args.app_root is None:
            return UnavailableWalletController()
        return VerifiedWalletController(args.app_root / "HolonWallet.exe")
    if args.wallet_path is None:
        return UnavailableWalletController()
    return VerifiedWalletController(args.wallet_path)


def _restore_revalidated_signing(lifecycle: GuardLifecycle, failure: str | None) -> None:
    if (
        failure is None
        and lifecycle.snapshot.state.value == "SIGNING_DISABLED"
        and lifecycle.snapshot.reason in RECOVERABLE_SIGNING_REASONS
    ):
        lifecycle.enable_signing("AAVE_CAPABILITY_READY")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    data_dir = args.data_dir or _default_data_dir()
    try:
        with SingleInstanceLock(data_dir / "guard.lock"):
            store = SnapshotStore(data_dir / "guard-state.json")
            action_store = ActionStateStore(data_dir / "action-state.json")
            lending_operation_store = LendingOperationStore(
                data_dir / "lending-operation-state.json",
            )
            action_failure: str | None = None
            try:
                action_snapshot = action_store.load()
            except (MissingActionState, InvalidActionState):
                action_snapshot = ActionStateSnapshot(None, ())
                action_failure = SecurityCode.ACTION_STATE_INVALID.value
            operation_failure: str | None = None
            try:
                lending_operation_snapshot = lending_operation_store.load()
            except LendingOperationStateError:
                lending_operation_snapshot = LendingOperationSnapshot()
                operation_failure = "LENDING_OPERATION_STATE_INVALID"
            policy_failure: str | None = None
            try:
                baseline_policy = load_baseline_policy(_policy_path(args))
            except PolicyLoadError as exc:
                baseline_policy = Policy("2", "1", False, ())
                policy_failure = exc.code
            revision_store = PolicyRevisionStore(data_dir, baseline_policy)
            revision_invalid = False
            try:
                policy_snapshot, _migrated = revision_store.migrate_to_v4()
            except PolicyRevisionUnavailable:
                policy_snapshot = revision_store.recoverable_snapshot()
                revision_invalid = True
            install_failure = _integrity_failure(args)
            module_registry = (
                CapabilityRegistry(catalog_error=install_failure)
                if install_failure is not None
                else load_module_registry(
                    args.module_catalog or default_catalog_path(), "guard",
                )
            )
            ledger = ActionLedger(action_store, action_snapshot)
            wallet = _wallet_controller(args, install_failure)
            lifecycle = GuardLifecycle.restore(
                store,
                wallet,
                WindowsOwnerProbe(),
                ledger,
                lending_operation_store,
                lending_operation_snapshot,
            )
            audit, audit_failure = load_authority_audit(data_dir)
            promotion_blocker = (
                install_failure or audit_failure or policy_failure or action_failure
                or operation_failure
            )
            failure = promotion_blocker or (
                SecurityCode.POLICY_STATE_INVALID.value if revision_invalid else None
            )
            if failure is not None:
                lifecycle.disable_signing(failure)
            else:
                _restore_revalidated_signing(lifecycle, failure)
            lending_reader = LendingReadService.default()
            history_store = HistoryStore(WalletPaths(data_dir))

            def lending_history(address: str):
                try:
                    return lending_cashflows_for_address(history_store.load(), address)
                except HistoryUnavailableError:
                    return None

            lending_portfolio = LendingPortfolioService(
                lending_reader,
                LendingAnalyticsStore(data_dir / "lending-analytics.json"),
            )
            earn_registry = EarnProviderRegistry()
            earn_registry.register(LendingEarnProvider(lending_portfolio))
            register_module_providers(earn_registry, module_registry)
            earn_portfolio = EarnPortfolioService(
                earn_registry, EarnSnapshotStore(data_dir / "earn-snapshots.json"),
            )

            authority = AuthorityService(
                lifecycle, PolicyEngine(policy_snapshot.policy), audit,
                security_failure=failure, policy_snapshot=policy_snapshot,
                revision_store=revision_store,
                lending=lending_reader,
                lending_actions=ActionProfilesState.load(),
                lending_portfolio=lending_portfolio,
                earn_portfolio=earn_portfolio,
                lending_history=lending_history,
                module_registry=module_registry,
                module_data_dir=data_dir,
            )
            if lifecycle.snapshot.state.value == "SIGNING_DISABLED":
                authority.audit_system(
                    EventType.SIGNING_DISABLED, lifecycle.snapshot.reason,
                    guard_state=lifecycle.snapshot.state.value,
                )
            wallet_path = getattr(wallet, "wallet_path", None)
            status_server = WalletStatusServer(
                authority.accept_wallet_status,
                lambda: (lifecycle.snapshot.wallet_pid, wallet_path),
                pipe_name=args.wallet_status_pipe_name,
                invalid_handler=lifecycle.wallet_status_mismatch,
            )
            policy_handler = GuardPolicyControl(
                revision_store,
                TrustedPolicyDraftStore(WalletPaths(data_dir)),
                authority,
                promotion_blocker=promotion_blocker,
                revision_invalid=revision_invalid,
                provisioner=AuthorityStateProvisioner(data_dir, revision_store),
                provisioning_blocker=(
                    install_failure or policy_failure or (
                        SecurityCode.POLICY_STATE_INVALID.value
                        if revision_invalid else None
                    )
                ),
            )
            policy_server = (
                PolicyControlServer(
                    policy_handler.handle, wallet_path,
                    pipe_name=args.policy_control_pipe_name,
                )
                if wallet_path is not None else None
            )
            GuardServer(
                args.pipe_name, authority, status_server=status_server,
                policy_server=policy_server,
            ).serve_forever()
    except GuardAlreadyRunning:
        return 3
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
