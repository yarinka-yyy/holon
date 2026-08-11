"""Run PyInstaller without leaking its ordinary diagnostics to PowerShell stderr."""

from __future__ import annotations

from subprocess import STDOUT, run
import sys


def main() -> int:
    return run(
        [sys.executable, "-m", "PyInstaller", *sys.argv[1:]], stderr=STDOUT,
    ).returncode


if __name__ == "__main__":
    raise SystemExit(main())
