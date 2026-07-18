from __future__ import annotations

from argparse import Namespace
import os
from pathlib import Path

from holon_contracts import MessageKind, SecurityCode, make_envelope
from holon_guard import GuardLifecycle, SnapshotStore
from holon_guard.__main__ import _integrity_failure
from holon_guard.authority import AuthorityService
from holon_policy import PolicyEngine
from guard_support import enabled_policy, make_audit, make_ledger, transfer_request
from package_support import build_fixture, install_fixture


class NoLaunchWallet:
    calls = 0

    def open_or_activate(self, flow_id: str):
        del flow_id
        self.calls += 1
        raise AssertionError("Wallet must not launch")

    def request_close(self, handle: object) -> None:
        del handle


class LiveOwner:
    def is_alive(self, pid: int) -> bool:
        return pid == os.getpid()


def _args(manifest: Path, app: Path, plugin: Path, version: str = "0.18.2") -> Namespace:
    return Namespace(
        require_install_integrity=True, manifest_path=manifest,
        app_root=app, plugin_root=plugin, hermes_version=version,
    )


def test_guard_entry_integrity_gate_recovers_after_clean_reinstall(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    manifest, app, plugin = install_fixture(package, tmp_path / "installed")
    assert _integrity_failure(_args(manifest, app, plugin)) is None
    guard = app / "HolonGuard.exe"
    guard.write_bytes(b"tampered")
    assert _integrity_failure(_args(manifest, app, plugin)) == "GUARD_INTEGRITY_FAILED"
    guard.write_bytes((package / "payload" / "app" / "HolonGuard.exe").read_bytes())
    assert _integrity_failure(_args(manifest, app, plugin)) is None
    assert _integrity_failure(_args(manifest, app, plugin, "0.19.0")) == (
        "HERMES_VERSION_UNSUPPORTED"
    )
    assert _integrity_failure(_args(manifest, app, plugin, "")) == "HERMES_VERSION_UNSUPPORTED"


def test_integrity_failure_keeps_health_readable_and_blocks_authority(tmp_path: Path) -> None:
    store = SnapshotStore(tmp_path / "guard-state.json")
    store.bootstrap_normal_for_test()
    wallet = NoLaunchWallet()
    lifecycle = GuardLifecycle(store, store.load(), wallet, LiveOwner(), make_ledger(tmp_path))
    code = SecurityCode.PLUGIN_INTEGRITY_FAILED.value
    lifecycle.disable_signing(code)
    service = AuthorityService(
        lifecycle, PolicyEngine(enabled_policy().policy), make_audit(tmp_path), code,
    )
    health = service.handle(make_envelope(MessageKind.HEALTH_REQUEST, {}), None)
    refused = service.handle(transfer_request(), os.getpid())
    assert health.kind is MessageKind.HEALTH_RESPONSE
    assert health.payload["code"] == code and not health.payload["authority_available"]
    assert refused.kind is MessageKind.SIGNING_DISABLED and refused.payload["code"] == code
    assert wallet.calls == 0
