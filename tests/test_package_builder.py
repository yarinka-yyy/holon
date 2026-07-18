from __future__ import annotations

from pathlib import Path

import pytest

from holon_guard.action_model import ActionStateSnapshot
from holon_guard.model import GuardSnapshot
from holon_guard.request_model import RequestControlSnapshot
from holon_installation import BuildError, PackageBuilder
from package_support import SOURCE_ROOT, build_fixture


def test_builder_creates_fixed_layout_and_valid_initial_state(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    assert (package / "payload" / "app" / "HolonGuard.exe").is_file()
    assert (package / "payload" / "app" / "HolonWallet.exe").is_file()
    plugin = package / "payload" / "plugin"
    assert (plugin / "plugin.yaml").is_file()
    assert (plugin / "holon_contracts" / "__init__.py").is_file()
    assert (plugin / "holon_guard_ipc" / "__init__.py").is_file()
    data = package / "payload" / "initial-data"
    guard = GuardSnapshot.from_dict(__import__("json").loads((data / "guard-state.json").read_text()))
    action = ActionStateSnapshot.from_dict(__import__("json").loads((data / "action-state.json").read_text()))
    request = RequestControlSnapshot.from_dict(
        __import__("json").loads((data / "request-control-state.json").read_text())
    )
    assert guard.state.value == "NORMAL"
    assert action.current is None and not action.terminal
    assert not request.attempts and (data / "journal.jsonl").read_bytes() == b""


def test_builder_requires_both_injected_artifacts(tmp_path: Path) -> None:
    with pytest.raises(BuildError, match="required"):
        PackageBuilder(SOURCE_ROOT).build(tmp_path / "package", {})


def test_builder_refuses_a_real_package_before_future_binaries(tmp_path: Path) -> None:
    artifact = tmp_path / "binary.exe"
    artifact.write_bytes(b"not-a-real-binary")
    with pytest.raises(BuildError, match="unavailable"):
        PackageBuilder(SOURCE_ROOT).build(
            tmp_path / "package", {"guard": artifact, "wallet": artifact},
        )


def test_builder_refuses_nonempty_destination(tmp_path: Path) -> None:
    destination = tmp_path / "package"
    destination.mkdir()
    (destination / "canary").write_text("preserve", encoding="utf-8")
    artifact = tmp_path / "binary.exe"
    artifact.write_bytes(b"fixture")
    with pytest.raises(BuildError, match="empty"):
        PackageBuilder(SOURCE_ROOT).build(
            destination, {"guard": artifact, "wallet": artifact}, test_fixture=True,
        )
    assert (destination / "canary").read_text(encoding="utf-8") == "preserve"
