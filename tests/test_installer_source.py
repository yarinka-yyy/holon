from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "packaging" / "installer.iss"


def test_installer_has_fixed_per_user_bilingual_contract() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in source
    assert "DefaultDirName={localappdata}\\Holon\\installer" in source
    assert "DisableDirPage=yes" in source
    assert 'Name: "english"' in source
    assert 'Name: "russian"' in source
    assert "Holon-0.1.0-alpha-Setup" in source
    assert "VersionInfoVersion=0.1.0.0" in source
    assert "SignTool=" not in source


def test_installer_requires_safe_hermes_and_uses_transactional_backend() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "PrepareToInstall" in source
    assert "-RequireClosed" in source
    assert "-ConfirmHermesClosed -EnableHermesPlugin" in source
    assert "--no-allow-tool-override" not in source  # owned by install.ps1
    assert "HolonPackage\\install.ps1" in source
    assert "HolonPackage\\uninstall.ps1" not in source
    assert "-RemoveData -ConfirmDataDeletion" in source
    assert "Open Hermes and type /holon" in source
    assert "Откройте Hermes и введите /holon" in source


def test_installer_mentions_no_new_runtime_or_secret_channel() -> None:
    source = SOURCE.read_text(encoding="utf-8").lower()

    assert "mcp server" not in source
    assert "seed phrase" not in source
    assert "private key" not in source
    assert "wallet password" not in source
    assert "signed bytes" not in source


def test_icon_builder_writes_png_backed_windows_icon(tmp_path: Path) -> None:
    destination = tmp_path / "holon.ico"
    environment = dict(os.environ)
    environment["QT_QPA_PLATFORM"] = "offscreen"
    completed = subprocess.run(
        [
            sys.executable,
            str(ROOT / "packaging" / "build_icon.py"),
            str(ROOT / "src" / "holon_wallet" / "qml" / "assets" / "holon.svg"),
            str(destination),
        ],
        capture_output=True,
        check=False,
        env=environment,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr
    raw = destination.read_bytes()
    assert raw[:6] == b"\x00\x00\x01\x00\x01\x00"
    assert raw[22:30] == b"\x89PNG\r\n\x1a\n"
