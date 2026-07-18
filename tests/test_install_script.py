from __future__ import annotations

import json
from pathlib import Path

from package_support import build_fixture
from powershell_support import invoke, make_junction
from holon_installation import verify_installed


def _install(package: Path, local: Path, hermes: Path):
    return invoke(
        package / "install.ps1", "-PackageRoot", package,
        "-LocalAppDataRoot", local, "-HermesHome", hermes,
        "-ConfirmHermesClosed",
    )


def test_confirmation_is_required_and_result_is_safe(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    code, result = invoke(
        package / "install.ps1", "-PackageRoot", package,
        "-LocalAppDataRoot", tmp_path / "local", "-HermesHome", tmp_path / "hermes",
    )
    assert code == 2 and result["code"] == "HERMES_CLOSED_CONFIRMATION_REQUIRED"
    assert str(tmp_path) not in json.dumps(result)


def test_clean_install_bootstraps_data_and_reinstall_repairs_program(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    code, result = _install(package, local, hermes)
    assert code == 0 and result["code"] == "INSTALL_OK"
    app = local / "Holon" / "app"
    data = local / "Holon" / "data"
    plugin = hermes / "plugins" / "holon"
    assert (app / "HolonGuard.exe").read_bytes() == b"mock-guard-binary"
    assert (plugin / "plugin.yaml").is_file()
    assert json.loads((data / "guard-state.json").read_text())["state"] == "NORMAL"
    canaries = {
        "vault.canary": b"vault-secret-canary", "settings.canary": b"settings-canary",
        "journal.jsonl": b"journal-canary\n", "action-state.json": b"security-canary",
    }
    for name, value in canaries.items():
        (data / name).write_bytes(value)
    (app / "HolonGuard.exe").write_bytes(b"damaged")
    assert _install(package, local, hermes)[0] == 0
    assert (app / "HolonGuard.exe").read_bytes() == b"mock-guard-binary"
    assert verify_installed(
        app / "release-manifest.json", app, plugin, "0.18.2",
    ).ok
    for name, value in canaries.items():
        assert (data / name).read_bytes() == value


def test_tampered_payload_never_replaces_existing_program(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    assert _install(package, local, hermes)[0] == 0
    installed = local / "Holon" / "app" / "HolonGuard.exe"
    installed.write_bytes(b"existing-install-canary")
    (package / "payload" / "app" / "HolonGuard.exe").write_bytes(b"tampered-payload")
    code, result = _install(package, local, hermes)
    assert code == 2 and result["code"] == "INSTALL_VALIDATION_FAILED"
    assert installed.read_bytes() == b"existing-install-canary"


def test_preexisting_empty_data_directory_is_not_populated(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    data = local / "Holon" / "data"
    data.mkdir(parents=True)
    assert _install(package, local, hermes)[0] == 0
    assert list(data.iterdir()) == []

def test_reparse_point_payload_is_not_installed(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    plugin = package / "payload" / "plugin"
    target = package / "payload" / "plugin-real"
    plugin.rename(target)
    make_junction(plugin, target)
    try:
        code, result = _install(package, tmp_path / "local", tmp_path / "hermes")
        assert code == 2 and result["code"] == "INSTALL_VALIDATION_FAILED"
        assert not (tmp_path / "local" / "Holon" / "app").exists()
    finally:
        plugin.rmdir()
        target.rename(plugin)

def test_malformed_manifest_is_validation_refusal(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    (package / "release-manifest.json").write_bytes(b"{broken")
    code, result = _install(package, tmp_path / "local", tmp_path / "hermes")
    assert code == 2 and result["code"] == "INSTALL_VALIDATION_FAILED"
    assert not (tmp_path / "local" / "Holon" / "app").exists()

def test_wrong_manifest_types_are_validation_refusal(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    manifest = package / "release-manifest.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["files"][0] = None
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, result = _install(package, tmp_path / "local", tmp_path / "hermes")
    assert code == 2 and result["code"] == "INSTALL_VALIDATION_FAILED"

def test_noncanonical_manifest_order_is_refused(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    manifest = package / "release-manifest.json"
    value = json.loads(manifest.read_text(encoding="utf-8"))
    value["files"][0], value["files"][1] = value["files"][1], value["files"][0]
    manifest.write_text(json.dumps(value), encoding="utf-8")
    code, result = _install(package, tmp_path / "local", tmp_path / "hermes")
    assert code == 2 and result["code"] == "INSTALL_VALIDATION_FAILED"

def test_damaged_support_is_refused_before_import(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    (package / "InstallSupport.psm1").write_text("throw 'must-not-import'", encoding="utf-8")
    code, result = _install(package, tmp_path / "local", tmp_path / "hermes")
    assert code == 2 and result["code"] == "INSTALL_VALIDATION_FAILED"
    assert not (tmp_path / "local" / "Holon" / "app").exists()
