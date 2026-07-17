"""Plugin-side Guard boundary and bounded process launcher."""

from __future__ import annotations

import subprocess
import sys
from typing import Protocol

from holon_guard_ipc import (
    PROTECTED_STATES,
    GuardAvailability,
    GuardHealth,
    GuardState,
    PipeGuardClient,
)
from holon_guard_ipc.client import wait_for_pipe


class GuardClient(Protocol):
    def probe(self) -> GuardHealth: ...


class GuardLauncher(Protocol):
    def start(self) -> None: ...


class UnavailableGuardClient:
    def probe(self) -> GuardHealth:
        return GuardHealth.unavailable()


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
            list(self._command),
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            creationflags=creationflags,
        )
        try:
            wait_for_pipe(self._pipe_name, self._startup_timeout)
        except Exception:
            if process.poll() is None:
                process.terminate()
            raise


class GuardConnector:
    """Probe, optionally launch once, then probe once more."""

    def __init__(self, client: GuardClient, launcher: GuardLauncher) -> None:
        self._client = client
        self._launcher = launcher

    @staticmethod
    def _normalize(result: object) -> GuardHealth:
        if not isinstance(result, GuardHealth):
            return GuardHealth.uncertain()
        if result.availability is GuardAvailability.AVAILABLE:
            if result.state is GuardState.UNKNOWN:
                return GuardHealth.uncertain()
            return GuardHealth.available(result.state)
        if result.availability is GuardAvailability.UNAVAILABLE:
            return GuardHealth.unavailable()
        if result.availability is GuardAvailability.UNCERTAIN:
            return GuardHealth.uncertain()
        return GuardHealth.uncertain()

    def probe(self) -> GuardHealth:
        try:
            result = self._client.probe()
        except Exception:
            return GuardHealth.uncertain()
        return self._normalize(result)

    def ensure_available(self) -> GuardHealth:
        first = self.probe()
        if first.availability is not GuardAvailability.UNAVAILABLE:
            return first
        try:
            self._launcher.start()
        except Exception:
            return GuardHealth.unavailable()
        return self.probe()
