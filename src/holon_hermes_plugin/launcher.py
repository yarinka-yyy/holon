"""Bounded test and fixed installed Guard launchers."""

from __future__ import annotations

import os
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
        self, local_app_data: Path, plugin_root: Path, hermes_version: str,
        pipe_name: str = PIPE_NAME,
    ) -> None:
        app_root = local_app_data / "Holon" / "app"
        command = (
            str(app_root / "HolonGuard.exe"), "--require-install-integrity",
            "--manifest-path", str(app_root / "release-manifest.json"),
            "--app-root", str(app_root), "--plugin-root", str(plugin_root),
            "--hermes-version", hermes_version,
        )
        super().__init__(command, pipe_name)


def _installed_hermes_version(plugin_root: Path) -> str:
    """Read the selected Hermes installation's unambiguous local metadata."""
    site_packages = (
        plugin_root.parent.parent / "hermes-agent" / "venv" / "Lib"
        / "site-packages"
    )
    try:
        metadata_files = tuple(site_packages.glob("hermes_agent-*.dist-info/METADATA"))
    except OSError:
        return ""
    if len(metadata_files) != 1:
        return ""
    try:
        fields = {
            key.casefold(): value.strip()
            for line in metadata_files[0].read_text(encoding="utf-8").splitlines()
            if ":" in line
            for key, value in (line.split(":", 1),)
        }
    except OSError:
        return ""
    if fields.get("name", "").casefold() != "hermes-agent":
        return ""
    return fields.get("version", "")


def production_launcher() -> DisabledGuardLauncher | InstalledGuardLauncher:
    local_app_data = os.environ.get("LOCALAPPDATA")
    if not local_app_data:
        return DisabledGuardLauncher()
    plugin_root = Path(__file__).resolve().parent
    hermes_version = _installed_hermes_version(plugin_root)
    return InstalledGuardLauncher(Path(local_app_data), plugin_root, hermes_version)
