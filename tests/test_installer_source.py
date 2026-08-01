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
    assert "RedirectionGuard=no" in source
    assert "DefaultDirName={localappdata}\\Holon\\installer" in source
    assert "DisableDirPage=yes" in source
    assert "AllowCancelDuringInstall=no" in source
    assert 'Name: "english"' in source
    assert 'Name: "russian"' in source
    assert "Holon-0.1.0-alpha-Setup" in source
    assert "VersionInfoVersion=0.1.0.0" in source
    assert "SignTool=" not in source
    assert "InfoBeforeFile={#NOTICE_FILE}" in source
    assert "#error NOTICE_FILE is required" in source


def test_installer_support_accepts_only_the_fixed_license_bundle() -> None:
    support = (ROOT / "packaging" / "InstallSupport.psm1").read_text(encoding="utf-8")
    assert "payload/app/licenses/LICENSE" in support
    assert "payload/app/licenses/NOTICE" in support
    assert "payload/app/licenses/THIRD_PARTY_LICENSES.txt" in support


def test_installer_requires_safe_hermes_and_uses_transactional_backend() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "PrepareToInstall" in source
    assert "-RequireClosed" in source
    assert "-OutputPath" in source
    run_detector = source.split("function RunDetector", 1)[1].split("procedure RefreshHermesPage", 1)[0]
    assert "LoadStringsFromFile" in run_detector
    assert "ExecAndCaptureOutput" not in run_detector
    assert "SW_HIDE" in run_detector
    assert "TryNativeHermesRoot(GetEnv('HERMES_HOME')" in run_detector
    assert "TryNativeHermesRoot(" in run_detector
    assert "function ReadHermesVersion" in source
    assert "hermes_agent-*.dist-info" in source
    assert "Version: " in source
    assert "function IsCompatibleHermesVersion" in source
    assert "PatchVersion >= 2" in source
    assert "SELECT ProcessId, ExecutablePath FROM Win32_Process" in source
    assert "IsPathInsideHermes(ProcessPath, HermesHome)" in source
    assert "ProcessItem.Terminate(0)" in source
    close_processes = source.split("function CloseHermesProcesses", 1)[1].split(
        "function RunDetector", 1,
    )[0]
    assert "checking the selected installation again" in close_processes
    assert "Result := CountHermesProcesses(HermesHome) = 0;" in close_processes
    assert "          Exit;" not in close_processes
    assert "HermesClosePrompt" in source
    prepare = source.split("function PrepareToInstall", 1)[1].split(
        "function JoinOutput", 1
    )[0]
    assert "RunDetector(False, False, DetectionCode)" in prepare
    assert "CountHermesProcesses(DetectedHermesHome)" in prepare
    assert "CloseHermesProcesses(DetectedHermesHome)" in prepare
    assert "if ProcessCount > 0 then" in prepare
    assert "if ProcessCount = 0 then" not in prepare
    assert "ExtractTemporaryFiles('{tmp}\\HolonPackage\\*')" in prepare
    assert "InstallBackendCompleted := True" in prepare
    assert "RunInstallBackend(Details)" in prepare
    assert "procedure CurStepChanged" not in source
    assert "dontcopy noencryption recursesubdirs createallsubdirs" in source
    assert "-ConfirmHermesClosed -EnableHermesPlugin" in source
    assert "--no-allow-tool-override" not in source  # owned by install.ps1
    assert "HolonPackage\\install.ps1" in source
    assert "HolonPackage\\uninstall.ps1" not in source
    assert "-RemoveData -ConfirmDataDeletion" in source
    assert "Open Hermes and type /holon" in source
    assert "Откройте Hermes и введите /holon" in source
    assert "InstallBackendCompleted and" in source
    assert "' -HermesVersion ' + Quoted(DetectedHermesVersion)" in source
    run_backend = source.split("function RunInstallBackend", 1)[1].split(
        "function PrepareToInstall", 1
    )[0]
    assert "' -OutputPath ' + Quoted(ResultPath)" in run_backend
    assert "LoadStringsFromFile(ResultPath, Output)" in run_backend
    assert "Exec(PowerShellPath" in run_backend
    assert "ExecAndCaptureOutput" not in run_backend
    install_backend = (Path(__file__).parents[1] / "packaging" / "install.ps1").read_text(
        encoding="utf-8"
    )
    assert "function Test-HolHermesMetadataCompatibility([string]$HermesHomePath)" in install_backend
    assert "function Test-HolHermesCompatibility(" in install_backend
    assert "Test-HolHermesCompatibility $HermesVersion $HermesHome $HermesCommand" in install_backend
    assert "function Invoke-HolHermesEnable" in install_backend
    assert "[Diagnostics.ProcessStartInfo]::new()" in install_backend
    assert "$start.WorkingDirectory = $HermesHomePath" in install_backend
    assert '$start.EnvironmentVariables["HERMES_HOME"] = $HermesHomePath' in install_backend
    assert "function Write-HolInstallResult" in install_backend
    assert "$OutputPath" in install_backend


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
