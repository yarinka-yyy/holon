from __future__ import annotations

from pathlib import Path
import shutil
import struct

from holon_installation import PackageBuilder


SOURCE_ROOT = Path(__file__).parents[1]


def write_pe_fixture(path: Path, marker: bytes = b"") -> Path:
    raw = bytearray(512)
    raw[:2] = b"MZ"
    struct.pack_into("<I", raw, 0x3C, 0x80)
    raw[0x80:0x84] = b"PE\0\0"
    struct.pack_into("<H", raw, 0x84, 0x8664)
    struct.pack_into("<H", raw, 0x94, 0xF0)
    struct.pack_into("<H", raw, 0x96, 0x0002)
    struct.pack_into("<H", raw, 0x98, 0x20B)
    raw.extend(marker)
    path.write_bytes(raw)
    return path


def build_fixture(root: Path):
    artifacts = root / "artifacts"
    artifacts.mkdir()
    guard = artifacts / "guard.exe"
    wallet = artifacts / "wallet.exe"
    guard.write_bytes(b"mock-guard-binary")
    wallet.write_bytes(b"mock-wallet-binary")
    package = root / "package"
    manifest = PackageBuilder(SOURCE_ROOT).build(
        package, {"guard": guard, "wallet": wallet}, test_fixture=True,
    )
    return package, manifest


def install_fixture(package: Path, root: Path) -> tuple[Path, Path, Path]:
    app = root / "app"
    plugin = root / "plugin"
    shutil.copytree(package / "payload" / "app", app)
    shutil.copytree(package / "payload" / "plugin", plugin)
    manifest = app / "release-manifest.json"
    shutil.copy2(package / "release-manifest.json", manifest)
    return manifest, app, plugin
