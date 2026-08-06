from __future__ import annotations

import os
from pathlib import Path

import pytest

from holon_guard.action_model import ActionStateSnapshot
from holon_guard.model import GuardSnapshot
from holon_guard.request_model import RequestControlSnapshot
from holon_installation import BuildError, PackageBuilder
from package_support import SOURCE_ROOT, build_fixture, write_pe_fixture
from powershell_support import make_junction


def test_builder_creates_fixed_layout_and_valid_initial_state(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    assert (package / "payload" / "app" / "HolonGuard.exe").is_file()
    assert (package / "payload" / "app" / "HolonWallet.exe").is_file()
    licenses = package / "payload" / "app" / "licenses"
    assert {path.name for path in licenses.iterdir()} == {
        "LICENSE", "NOTICE", "THIRD_PARTY_LICENSES.txt",
    }
    assert "GNU LESSER GENERAL PUBLIC LICENSE" in (
        licenses / "THIRD_PARTY_LICENSES.txt"
    ).read_text(encoding="utf-8")
    plugin = package / "payload" / "plugin"
    assert (plugin / "plugin.yaml").is_file()
    assert (plugin / "holon_contracts" / "__init__.py").is_file()
    assert (plugin / "holon_earn" / "__init__.py").is_file()
    assert (plugin / "holon_contracts" / "network-assets.json").is_file()
    assert (plugin / "holon_guard_ipc" / "__init__.py").is_file()
    skills = package / "payload" / "skills" / "crypto"
    assert {path.parent.name for path in skills.glob("*/SKILL.md")} == {
        "holon", "holon-earn", "holon-lending",
    }
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


def test_builder_accepts_distinct_windows_x64_production_artifacts(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    guard = write_pe_fixture(artifacts / "HolonGuard.exe", b"guard")
    wallet = write_pe_fixture(artifacts / "HolonWallet.exe", b"wallet")
    manifest = PackageBuilder(SOURCE_ROOT).build(
        tmp_path / "package", {"guard": guard, "wallet": wallet},
    )
    assert manifest.manifest_version == "3"


@pytest.mark.parametrize("case", ["empty", "mock", "wrong_name", "wrong_arch", "same"])
def test_builder_refuses_invalid_production_artifacts(tmp_path: Path, case: str) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    guard_name = "guard.exe" if case == "wrong_name" else "HolonGuard.exe"
    guard = write_pe_fixture(artifacts / guard_name, b"guard")
    wallet = write_pe_fixture(artifacts / "HolonWallet.exe", b"wallet")
    if case == "empty":
        guard.write_bytes(b"")
    elif case == "mock":
        guard.write_bytes(b"not-a-real-binary")
    elif case == "wrong_arch":
        raw = bytearray(guard.read_bytes())
        raw[0x84:0x86] = b"\x4c\x01"
        guard.write_bytes(raw)
    elif case == "same":
        wallet.unlink()
        wallet = guard
    with pytest.raises(BuildError, match="artifact|distinct"):
        PackageBuilder(SOURCE_ROOT).build(
            tmp_path / "package", {"guard": guard, "wallet": wallet},
        )


def test_builder_refuses_production_artifact_link(tmp_path: Path) -> None:
    artifacts = tmp_path / "artifacts"
    artifacts.mkdir()
    target = write_pe_fixture(artifacts / "real-guard.exe", b"guard")
    guard = artifacts / "HolonGuard.exe"
    try:
        os.symlink(target, guard)
    except OSError:
        pytest.skip("File symlinks are unavailable")
    wallet = write_pe_fixture(artifacts / "HolonWallet.exe", b"wallet")
    with pytest.raises(BuildError, match="artifact"):
        PackageBuilder(SOURCE_ROOT).build(
            tmp_path / "package", {"guard": guard, "wallet": wallet},
        )


def test_builder_refuses_artifact_beneath_windows_reparse_directory(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction test")
    real = tmp_path / "real-artifacts"
    linked = tmp_path / "linked-artifacts"
    real.mkdir()
    guard = write_pe_fixture(real / "HolonGuard.exe", b"guard")
    wallet = write_pe_fixture(real / "HolonWallet.exe", b"wallet")
    make_junction(linked, real)
    try:
        with pytest.raises(BuildError, match="artifact"):
            PackageBuilder(SOURCE_ROOT).build(
                tmp_path / "package",
                {"guard": linked / guard.name, "wallet": linked / wallet.name},
            )
    finally:
        linked.rmdir()


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
