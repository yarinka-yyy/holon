from __future__ import annotations

from pathlib import Path

from package_support import build_fixture
from powershell_support import fake_hermes, invoke


def _uninstall(package: Path, local: Path, hermes: Path, command: Path, *extra: str):
    return invoke(
        package / "uninstall.ps1", "-LocalAppDataRoot", local,
        "-HermesHome", hermes, "-HermesCommand", command,
        "-ConfirmHermesClosed", *extra,
    )


def _installed(tmp_path: Path):
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    code, _ = invoke(
        package / "install.ps1", "-PackageRoot", package,
        "-LocalAppDataRoot", local, "-HermesHome", hermes, "-ConfirmHermesClosed",
    )
    assert code == 0
    return package, local, hermes, fake_hermes(tmp_path / "hermes-fixture.ps1")


def test_ordinary_uninstall_preserves_data(tmp_path: Path) -> None:
    package, local, hermes, command = _installed(tmp_path)
    data = local / "Holon" / "data"
    (data / "vault.canary").write_bytes(b"preserve")
    code, result = _uninstall(package, local, hermes, command)
    assert code == 0 and result["code"] == "UNINSTALL_OK"
    assert not (local / "Holon" / "app").exists()
    assert not (hermes / "plugins" / "holon").exists()
    assert (data / "vault.canary").read_bytes() == b"preserve"


def test_data_removal_requires_both_explicit_flags(tmp_path: Path) -> None:
    package, local, hermes, command = _installed(tmp_path)
    data = local / "Holon" / "data"
    code, result = _uninstall(package, local, hermes, command, "-RemoveData")
    assert code == 2 and result["code"] == "DATA_DELETION_CONFIRMATION_REQUIRED"
    assert data.exists()
    code, _ = _uninstall(
        package, local, hermes, command, "-RemoveData", "-ConfirmDataDeletion",
    )
    assert code == 0 and not data.exists()


def test_damaged_uninstaller_support_does_not_remove_install(tmp_path: Path) -> None:
    package, local, hermes, command = _installed(tmp_path)
    (package / "InstallSupport.psm1").write_text("throw 'must-not-import'", encoding="utf-8")
    code, result = _uninstall(package, local, hermes, command)
    assert code == 2 and result["code"] == "UNINSTALL_VALIDATION_FAILED"
    assert (local / "Holon" / "app" / "HolonGuard.exe").is_file()
