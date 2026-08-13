from __future__ import annotations

from pathlib import Path
import shutil

from holon_installation import verify_installed
from package_support import build_fixture, install_fixture


def test_clean_installed_files_are_eligible(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    manifest, app, plugin = install_fixture(package, tmp_path / "installed")
    assert verify_installed(manifest, app, plugin, "0.18.2").ok
    assert verify_installed(manifest, app, plugin, "0.20.0").ok


def test_critical_mismatch_and_hermes_version_fail_closed(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    manifest, app, plugin = install_fixture(package, tmp_path / "installed")
    (plugin / "plugin.py").write_text("tampered", encoding="utf-8")
    assert verify_installed(manifest, app, plugin, "0.18.2").code == "PLUGIN_INTEGRITY_FAILED"
    shutil.copy2(package / "payload" / "plugin" / "plugin.py", plugin / "plugin.py")
    assert verify_installed(manifest, app, plugin, "0.19.0").code == "HERMES_VERSION_UNSUPPORTED"
    assert verify_installed(manifest, app, plugin, "0.21.0").code == "HERMES_VERSION_UNSUPPORTED"


def test_missing_app_module_content_fails_installed_integrity(tmp_path: Path) -> None:
    from holon_modules import build_composition
    composition = tmp_path / "extended"
    build_composition(
        composition, "extended", [Path(__file__).parents[1] / "modules" / "perpdex"],
    )
    package = tmp_path / "package"
    artifacts = {name: tmp_path / f"{name}.exe" for name in ("guard", "wallet")}
    for artifact in artifacts.values():
        artifact.write_bytes(b"fixture")
    from holon_installation import PackageBuilder
    PackageBuilder(Path(__file__).parents[1], composition).build(
        package, artifacts, test_fixture=True,
    )
    manifest, app, plugin = install_fixture(package, tmp_path / "installed")
    (app / "modules" / "holon.perpdex" / "module-manifest.json").unlink()
    assert verify_installed(manifest, app, plugin, "0.18.2").code == "MODULES_INTEGRITY_FAILED"
