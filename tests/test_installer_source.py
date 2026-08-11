from __future__ import annotations

import os
from pathlib import Path
import subprocess
import struct
import sys

from PySide6.QtGui import QImage


ROOT = Path(__file__).parents[1]
SOURCE = ROOT / "packaging" / "installer.iss"


def test_installer_has_fixed_per_user_bilingual_contract() -> None:
    source = SOURCE.read_text(encoding="utf-8")

    assert "PrivilegesRequired=lowest" in source
    assert "RedirectionGuard=no" in source
    assert "DefaultDirName={localappdata}\\Holon\\installer" in source
    assert "DisableDirPage=yes" in source
    assert "AllowCancelDuringInstall=no" in source
    assert "UsePreviousLanguage=yes" in source
    assert "LanguageDetectionMethod=none" in source
    assert 'Name: "english"' in source
    assert 'Name: "russian"' in source
    assert "Holon-0.2.0-alpha-Setup" in source
    assert "VersionInfoVersion=0.2.0.0" in source
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
    assert "CountHolonGuardProcesses" in prepare
    assert "CloseHolonGuardProcesses" in prepare
    assert "IsInstalledHolonGuard" in source
    assert "{localappdata}\\Holon\\app\\HolonGuard.exe" in source
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
    assert "function Test-HolGuardRunning" in install_backend
    assert 'Stop-HolInstall 2 "HOLON_RUNTIME_RUNNING"' in install_backend
    assert "$OutputPath" in install_backend


def test_installer_mentions_no_new_runtime_or_secret_channel() -> None:
    source = SOURCE.read_text(encoding="utf-8").lower()

    assert "mcp server" not in source
    assert "seed phrase" not in source
    assert "private key" not in source
    assert "wallet password" not in source
    assert "signed bytes" not in source


def test_icon_builder_writes_transparent_multi_size_windows_icon(tmp_path: Path) -> None:
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
    reserved, kind, count = struct.unpack_from("<HHH", raw)
    assert (reserved, kind, count) == (0, 1, 9)
    expected_sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    frames = []
    for index, expected in enumerate(expected_sizes):
        width, height, _colors, _reserved, planes, bits, length, offset = (
            struct.unpack_from("<BBBBHHII", raw, 6 + 16 * index)
        )
        assert (width or 256, height or 256) == (expected, expected)
        assert (planes, bits) == (1, 32)
        png = raw[offset:offset + length]
        assert png[:8] == b"\x89PNG\r\n\x1a\n"
        frames.append(QImage.fromData(png, "PNG"))
    assert all(not frame.isNull() for frame in frames)
    assert all(frame.pixelColor(0, 0).alpha() == 0 for frame in frames)
    assert all(frame.pixelColor(frame.width() // 2, frame.height() // 2).alpha() > 0 for frame in frames)
    largest = frames[-1]
    visible = [
        (x, y)
        for y in range(largest.height())
        for x in range(largest.width())
        if largest.pixelColor(x, y).alpha() > 0
    ]
    left, right = min(x for x, _y in visible), max(x for x, _y in visible)
    top, bottom = min(y for _x, y in visible), max(y for _x, y in visible)
    assert right - left + 1 >= 248
    assert bottom - top + 1 >= 248
    frame64 = frames[6]
    assert frame64.pixelColor(32, 29).lightness() < 80
    assert frame64.pixelColor(32, 36).lightness() > 200
    assert frame64.pixelColor(32, 43).lightness() < 80
    frame16 = frames[0]
    assert frame16.pixelColor(8, 7).lightness() < 100
    assert frame16.pixelColor(8, 9).lightness() > 180


def test_holon_wallet_svg_matches_the_approved_flat_palette() -> None:
    source = (
        ROOT / "src" / "holon_wallet" / "qml" / "assets" / "holon.svg"
    ).read_text(encoding="utf-8")

    assert 'viewBox="0 0 64 64"' in source
    assert "#131B21" in source
    assert "#84C7BA" in source
    assert "#F2F3F1" in source
    assert 'id="icon-tile" x="1" y="1" width="62" height="62"' in source
    assert 'id="wallet-body" x="7" y="14" width="49" height="43"' in source
    assert 'id="holon-h"' in source
    assert 'id="wallet-clasp" x="47"' in source
    assert "<circle" in source
    assert all(item not in source for item in ("<linearGradient", "<filter", "<image"))
