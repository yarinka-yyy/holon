from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess

from powershell_support import POWERSHELL, fake_hermes


SCRIPT = Path(__file__).parents[1] / "packaging" / "detect-hermes.ps1"


def _detect(
    local: Path, home: Path, command: Path, *extra: str, output_path: Path | None = None,
) -> tuple[int, dict[str, str]]:
    environment = os.environ.copy()
    environment["HERMES_HOME"] = ""
    environment["PATH"] = os.pathsep.join(
        item for item in environment.get("PATH", "").split(os.pathsep)
        if "hermes" not in item.casefold()
    )
    arguments = [
            POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(SCRIPT),
            "-LocalAppDataRoot", str(local), "-HermesHomeOverride", str(home),
            "-HermesCommandOverride", str(command), *extra,
    ]
    if output_path is not None:
        arguments.extend(["-OutputPath", str(output_path)])
    completed = subprocess.run(
        arguments,
        capture_output=True, text=True, encoding="utf-8", timeout=20, env=environment,
    )
    assert not completed.stdout.startswith("\ufeff")
    text = output_path.read_text(encoding="utf-8") if output_path is not None else completed.stdout
    fields = dict(line.split("=", 1) for line in text.splitlines())
    assert set(fields) == {
        "code", "hermes_home", "hermes_command", "hermes_desktop", "version",
    }
    return completed.returncode, fields


def test_detects_compatible_custom_hermes_home(tmp_path: Path) -> None:
    home = tmp_path / "custom-hermes"
    home.mkdir()
    command = fake_hermes(tmp_path / "hermes-fixture.ps1")
    code, result = _detect(tmp_path / "local", home, command)
    assert code == 0 and result["code"] == "HERMES_READY"
    assert result["hermes_home"] == str(home)
    assert result["hermes_command"] == str(command)
    assert result["version"] == "0.18.2"


def test_detector_writes_public_result_file_without_stdout(tmp_path: Path) -> None:
    home = tmp_path / "custom-hermes"
    home.mkdir()
    output = tmp_path / "detector-result.txt"
    command = fake_hermes(tmp_path / "hermes-fixture.ps1")
    code, result = _detect(tmp_path / "local", home, command, output_path=output)
    assert code == 0 and result["code"] == "HERMES_READY"
    assert output.read_bytes().startswith(b"code=HERMES_READY\r\n")


def test_detector_accepts_the_checked_0_20_runtime(tmp_path: Path) -> None:
    home = tmp_path / "hermes-0-20"
    home.mkdir()
    command = fake_hermes(tmp_path / "hermes-0-20.ps1", version="0.20.0")

    code, result = _detect(tmp_path / "local", home, command)

    assert code == 0 and result["code"] == "HERMES_READY"
    assert result["version"] == "0.20.0"


def test_refuses_missing_or_incompatible_hermes(tmp_path: Path) -> None:
    missing_code, missing = _detect(
        tmp_path / "local", tmp_path / "missing", tmp_path / "missing.exe",
    )
    assert missing_code == 2 and missing["code"] == "HERMES_NOT_FOUND"
    for version in ("0.17.9", "0.18.1", "0.19.0", "0.21.0", "0.20.0.1"):
        home = tmp_path / ("hermes-" + version.replace(".", "-"))
        home.mkdir()
        command = fake_hermes(tmp_path / ("hermes-" + version.replace(".", "-") + ".ps1"), version=version)
        code, result = _detect(tmp_path / "local", home, command)
        assert code == 2 and result["code"] == "HERMES_INCOMPATIBLE"


def test_require_closed_detects_process_under_selected_home(tmp_path: Path) -> None:
    home = tmp_path / "custom-hermes"
    home.mkdir()
    runtime = home / "runtime.exe"
    shutil.copy2(shutil.which(POWERSHELL) or POWERSHELL, runtime)
    command = fake_hermes(tmp_path / "hermes-fixture.ps1")
    process = subprocess.Popen(
        [str(runtime), "-NoProfile", "-Command", "Start-Sleep -Seconds 10"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=0x08000000 if os.name == "nt" else 0,
    )
    try:
        code, result = _detect(
            tmp_path / "local", home, command, "-RequireClosed",
        )
        assert code == 2 and result["code"] == "HERMES_RUNNING"
    finally:
        process.terminate()
        process.wait(timeout=5)
