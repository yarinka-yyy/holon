from __future__ import annotations

import json
from pathlib import Path
import subprocess

import pytest

from package_support import build_fixture
from powershell_support import POWERSHELL, fake_hermes, invoke, make_junction
from holon_installation import verify_installed


def _install(
    package: Path, local: Path, hermes: Path, command: Path | None = None,
    *, enable: bool = False, verified_version: str | None = None,
):
    arguments: list[object] = [
        package / "install.ps1", "-PackageRoot", package,
        "-LocalAppDataRoot", local, "-HermesHome", hermes,
        "-ConfirmHermesClosed",
    ]
    if command is not None:
        arguments.extend(["-HermesCommand", command])
    if verified_version is not None:
        arguments.extend(["-HermesVersion", verified_version])
    if enable:
        arguments.append("-EnableHermesPlugin")
    return invoke(*arguments)


def _compatible_hermes_home(home: Path, version: str = "0.18.2") -> None:
    metadata = home / "hermes-agent" / "venv" / "Lib" / "site-packages" / (
        f"hermes_agent-{version}.dist-info"
    )
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        f"Name: hermes-agent\nVersion: {version}\n", encoding="utf-8",
    )


def test_confirmation_is_required_and_result_is_safe(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    code, result = invoke(
        package / "install.ps1", "-PackageRoot", package,
        "-LocalAppDataRoot", tmp_path / "local", "-HermesHome", tmp_path / "hermes",
    )
    assert code == 2 and result["code"] == "HERMES_CLOSED_CONFIRMATION_REQUIRED"
    assert str(tmp_path) not in json.dumps(result)


def test_filesystem_failure_reports_safe_exact_install_step(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    local.mkdir()
    (local / "Holon").write_text("not-a-directory", encoding="utf-8")

    code, result = _install(package, local, hermes)

    assert code == 3 and result["code"] == "INSTALL_FILESYSTEM_FAILED"
    assert result["message"] == "Installation could not be completed at stage_app."
    assert str(tmp_path) not in json.dumps(result)


def test_clean_install_bootstraps_data_and_reinstall_repairs_program(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    code, result = _install(package, local, hermes)
    assert code == 0 and result["code"] == "INSTALL_OK"
    app = local / "Holon" / "app"
    data = local / "Holon" / "data"
    plugin = hermes / "plugins" / "holon"
    skills = hermes / "skills" / "crypto"
    assert (app / "HolonGuard.exe").read_bytes() == b"mock-guard-binary"
    assert (plugin / "plugin.yaml").is_file()
    assert (skills / "holon" / "SKILL.md").is_file()
    assert (skills / "holon-lending" / "SKILL.md").is_file()
    assert json.loads((data / "guard-state.json").read_text())["state"] == "NORMAL"
    canaries = {
        "vault.canary": b"vault-secret-canary", "settings.canary": b"settings-canary",
        "journal.jsonl": b"journal-canary\n", "action-state.json": b"security-canary",
    }
    for name, value in canaries.items():
        (data / name).write_bytes(value)
    (app / "HolonGuard.exe").write_bytes(b"damaged")
    (skills / "holon" / "SKILL.md").write_text("damaged", encoding="utf-8")
    assert _install(package, local, hermes)[0] == 0
    assert (app / "HolonGuard.exe").read_bytes() == b"mock-guard-binary"
    assert verify_installed(
        app / "release-manifest.json", app, plugin, "0.18.2",
    ).ok
    assert (skills / "holon" / "SKILL.md").read_bytes() == (
        package / "payload" / "skills" / "crypto" / "holon" / "SKILL.md"
    ).read_bytes()
    for name, value in canaries.items():
        assert (data / name).read_bytes() == value


@pytest.mark.parametrize("version", ["0.18.2", "0.18.99", "0.20.0", "0.20.99"])
def test_enable_accepts_each_supported_hermes_version(tmp_path: Path, version: str) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    _compatible_hermes_home(hermes, version)

    code, result = _install(
        package, local, hermes,
        fake_hermes(tmp_path / "hermes.ps1", version=version), enable=True,
    )

    assert code == 0 and result["code"] == "INSTALL_OK"


@pytest.mark.parametrize("version", ["0.18.1", "0.19.0", "0.19.99", "0.21.0", "0.20.0.1"])
def test_enable_rejects_each_unsupported_verified_version(tmp_path: Path, version: str) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    _compatible_hermes_home(hermes, "0.20.0")

    code, result = _install(
        package, local, hermes,
        fake_hermes(tmp_path / "hermes.ps1", version="0.20.0"),
        enable=True, verified_version=version,
    )

    assert code == 2 and result["code"] == "INSTALL_VALIDATION_FAILED"
    assert not (hermes / "plugins" / "holon").exists()


def test_result_file_is_utf8_json_and_stdout_stays_empty(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    result_path = tmp_path / "install-result.json"
    completed = subprocess.run(
        [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(package / "install.ps1"), "-PackageRoot", str(package),
            "-LocalAppDataRoot", str(local), "-HermesHome", str(hermes),
            "-OutputPath", str(result_path), "-ConfirmHermesClosed",
        ],
        capture_output=True, text=True, encoding="utf-8", timeout=20,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout == ""
    assert not result_path.read_bytes().startswith(b"\xef\xbb\xbf")
    assert json.loads(result_path.read_text(encoding="utf-8")) == {
        "ok": True, "code": "INSTALL_OK",
        "message": "Holon base package installed.",
    }


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


def test_enable_plugin_not_found_restores_previous_app_plugin_and_skills(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    assert _install(package, local, hermes)[0] == 0
    _compatible_hermes_home(hermes)
    canaries = {
        local / "Holon" / "app" / "HolonGuard.exe": b"old-app",
        hermes / "plugins" / "holon" / "plugin.yaml": b"old-plugin",
        hermes / "skills" / "crypto" / "holon" / "SKILL.md": b"old-holon-skill",
        hermes / "skills" / "crypto" / "holon-lending" / "SKILL.md": b"old-lending-skill",
    }
    for path, value in canaries.items():
        path.write_bytes(value)
    command = fake_hermes(
        tmp_path / "hermes-fail.ps1", fail_enable=True,
        enable_output="Plugin 'holon' is not installed or bundled.",
    )
    code, result = _install(package, local, hermes, command, enable=True)
    assert code == 3 and result == {
        "ok": False,
        "code": "HERMES_ENABLE_PLUGIN_NOT_FOUND",
        "message": "Hermes could not find the staged Holon plugin. Previous files were restored.",
    }
    for path, value in canaries.items():
        assert path.read_bytes() == value


def test_enable_failure_never_discloses_hermes_output(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    _compatible_hermes_home(hermes)
    command = fake_hermes(
        tmp_path / "hermes-fail.ps1", fail_enable=True,
        enable_output="secret-value-must-not-appear",
    )

    code, result = _install(package, local, hermes, command, enable=True)

    assert code == 3 and result["code"] == "HERMES_ENABLE_BEFORE_CONFIG_UPDATE"
    assert "secret-value" not in json.dumps(result)


def test_enable_internal_error_reports_only_the_safe_error_class(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    _compatible_hermes_home(hermes)
    command = fake_hermes(
        tmp_path / "hermes-fail.ps1", fail_enable=True,
        enable_output="RuntimeError: secret-value-must-not-appear",
    )

    code, result = _install(package, local, hermes, command, enable=True)

    assert code == 3 and result == {
        "ok": False,
        "code": "HERMES_ENABLE_INTERNAL_ERROR",
        "message": "Hermes stopped with an internal RuntimeError before enabling Holon. Previous files were restored.",
    }
    assert "secret-value" not in json.dumps(result)


def test_enable_unicode_encode_error_reports_only_safe_error_class(tmp_path: Path) -> None:
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    _compatible_hermes_home(hermes)
    command = fake_hermes(
        tmp_path / "hermes-fail.ps1", fail_enable=True,
        enable_output="UnicodeEncodeError: secret console encoding detail",
    )

    code, result = _install(package, local, hermes, command, enable=True)

    assert code == 3 and result == {
        "ok": False,
        "code": "HERMES_ENABLE_INTERNAL_ERROR",
        "message": "Hermes stopped with an internal UnicodeEncodeError before enabling Holon. Previous files were restored.",
    }
    assert "secret" not in json.dumps(result)


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
