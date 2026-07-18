from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess


POWERSHELL = "powershell.exe"


def invoke(script: Path, *arguments: object) -> tuple[int, dict]:
    command = [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)]
    command.extend(str(item) for item in arguments)
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", timeout=20,
    )
    assert not completed.stdout.startswith("\ufeff")
    lines = [line for line in completed.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, (completed.stdout, completed.stderr)
    payload = json.loads(lines[0])
    assert set(payload) == {"ok", "code", "message"}
    return completed.returncode, payload


def fake_hermes(path: Path) -> Path:
    path.write_text("param([Parameter(ValueFromRemainingArguments=$true)]$Args)\nexit 0\n", encoding="utf-8")
    return path


def make_junction(link: Path, target: Path) -> None:
    command = (
        "$null = New-Item -ItemType Junction "
        "-Path $env:HOLON_TEST_LINK -Target $env:HOLON_TEST_TARGET"
    )
    environment = os.environ.copy()
    environment["HOLON_TEST_LINK"] = str(link)
    environment["HOLON_TEST_TARGET"] = str(target)
    subprocess.run(
        [POWERSHELL, "-NoProfile", "-Command", command], env=environment,
        check=True, capture_output=True, text=True, timeout=10,
    )
