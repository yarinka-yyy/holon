from __future__ import annotations

import copy
import json
import os
from pathlib import Path

import pytest

from holon_installation import ManifestError, decode_manifest, verify_package
from holon_installation.codec import encode_manifest
from holon_installation.verify import hermes_supported
from package_support import build_fixture
from powershell_support import make_junction


def _value(package: Path) -> dict:
    return json.loads((package / "release-manifest.json").read_text(encoding="utf-8"))


def test_manifest_is_canonical_and_records_required_versions(tmp_path: Path) -> None:
    package, manifest = build_fixture(tmp_path)
    raw = (package / "release-manifest.json").read_bytes()
    assert encode_manifest(decode_manifest(raw)) == raw
    assert manifest.package_version == "0.1.0a0"
    assert set(manifest.component_versions) == {"plugin", "guard", "wallet", "contracts", "policy"}
    assert manifest.hermes_compatibility == ">=0.18.2,<0.19.0"
    assert verify_package(package).ok


@pytest.mark.parametrize(
    "change", ["unknown", "traversal", "absolute", "duplicate", "classification", "critical"],
)
def test_manifest_rejects_unsafe_or_unknown_content(tmp_path: Path, change: str) -> None:
    package, _ = build_fixture(tmp_path)
    value = _value(package)
    if change == "unknown":
        value["extra"] = True
    elif change == "traversal":
        value["files"][0]["path"] = "payload/../escape"
    elif change == "absolute":
        value["files"][0]["path"] = "C:/escape"
    elif change == "duplicate":
        duplicate = copy.deepcopy(value["files"][0])
        duplicate["path"] = duplicate["path"].upper()
        value["files"].append(duplicate)
        value["files"].sort(key=lambda item: item["path"].casefold())
    elif change == "classification":
        value["files"][0]["component"] = "wallet"
    else:
        value["files"][0]["critical"] = not value["files"][0]["critical"]
    with pytest.raises(ManifestError):
        decode_manifest(json.dumps(value, separators=(",", ":"), sort_keys=True).encode())


def test_manifest_rejects_duplicate_fields_and_oversize() -> None:
    with pytest.raises(ManifestError):
        decode_manifest(b'{"manifest_version":"1","manifest_version":"1"}')
    with pytest.raises(ManifestError, match="size"):
        decode_manifest(b"x" * (256 * 1024 + 1))


def test_tampering_returns_component_specific_code(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    (package / "payload" / "app" / "HolonGuard.exe").write_bytes(b"changed")
    result = verify_package(package)
    assert not result.ok
    assert result.code == "GUARD_INTEGRITY_FAILED"
    assert result.component == "guard"


def test_package_rejects_file_links(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    guard = package / "payload" / "app" / "HolonGuard.exe"
    target = tmp_path / "same-content.exe"
    target.write_bytes(guard.read_bytes())
    guard.unlink()
    try:
        os.symlink(target, guard)
    except OSError:
        pytest.skip("File symlinks are unavailable")
    result = verify_package(package)
    assert not result.ok and result.code == "GUARD_INTEGRITY_FAILED"


def test_package_rejects_windows_reparse_directory(tmp_path: Path) -> None:
    if os.name != "nt":
        pytest.skip("Windows junction test")
    package, _ = build_fixture(tmp_path)
    plugin = package / "payload" / "plugin"
    target = package / "payload" / "plugin-real"
    plugin.rename(target)
    make_junction(plugin, target)
    try:
        result = verify_package(package)
        assert not result.ok and result.code in {
            "PLUGIN_INTEGRITY_FAILED", "CONTRACT_INTEGRITY_FAILED",
        }
    finally:
        plugin.rmdir()
        target.rename(plugin)


def test_hermes_compatibility_range_is_exact() -> None:
    assert hermes_supported("0.18.2")
    assert hermes_supported("0.18.99")
    assert not hermes_supported("0.18.1")
    assert not hermes_supported("0.19.0")
    assert not hermes_supported("00.18.2")
    assert not hermes_supported("unknown")
