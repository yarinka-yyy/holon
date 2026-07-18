"""Bounded test and fixed installed Guard launchers."""

from __future__ import annotations

import os
from importlib import metadata
from pathlib import Path
import subprocess
import sys

from holon_guard_ipc import PIPE_NAME
from holon_guard_ipc.client import wait_for_pipe


class DisabledGuardLauncher:
    def start(self) -> None:
        raise RuntimeError("Guard implementation is not installed")


class SubprocessGuardLauncher:
    def __init__(
        self, command: tuple[str, ...], pipe_name: str, startup_timeout: float = 3.0
    ) -> None:
        if not command:
            raise ValueError("Guard command must not be empty")
        self._command = command
        self._pipe_name = pipe_name
        self._startup_timeout = startup_timeout

    def start(self) -> None:
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        process = subprocess.Popen(
            list(self._command), shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, creationflags=creationflags,
        )
        try:
            wait_for_pipe(self._pipe_name, self._startup_timeout)
        except Exception:
            if process.poll() is None:
                process.terminate()
            raise


class InstalledGuardLauncher(SubprocessGuardLauncher):
    """Launch only the fixed per-user installed Guard with integrity required."""

    def __init__(
        self, local_app_data: Path, hermes_version: str,
        pipe_name: str = PIPE_NAME,
    ) -> None:
        app_root = local_app_data / "Holon" / "app"
        plugin_root = local_app_data / "hermes" / "plugins" / "holon"
        command = (
            str(app_root / "HolonGuard.exe"), "--require-install-integrity",
            "--manifest-path", str(app_root / "release-manifest.json"),
            "--app-root", str(app_root), "--plugin-root", str(plugin_root),
            "--hermes-version", hermes_version,
        )
        super().__init__(command, pipe_name)


def production_launcher() -> DisabledGuardLauncher | InstalledGuardLauncher:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return DisabledGuardLauncher()
    try:
        hermes_version = metadata.version("hermes-agent")
    except Exception:
        hermes_version = ""
    return InstalledGuardLauncher(Path(local_app_data), hermes_version)
