from __future__ import annotations

from pathlib import Path
import shutil

from holon_installation import PackageBuilder


SOURCE_ROOT = Path(__file__).parents[1]


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
