"""Wallet process seam; M2.02 uses only injected mock implementations."""

from __future__ import annotations

import ctypes
import subprocess
import sys
import uuid
from dataclasses import dataclass
from ctypes import wintypes
from pathlib import Path
from typing import Callable, Protocol

from holon_wallet_control import (
    ControlProtocolError,
    ControlUnavailable,
    WalletControlClient,
    WalletPublicClient,
)


class WalletHandle(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...


class WalletController(Protocol):
    def open_public(self) -> "WalletOpenResult": ...

    def read_public_balances(self) -> "WalletBalancesResult": ...

    def open_or_activate(self, flow_id: str) -> WalletHandle: ...

    def request_close(self, handle: WalletHandle) -> None: ...


class OwnerProbe(Protocol):
    def is_alive(self, pid: int) -> bool: ...


@dataclass(frozen=True)
class WalletOpenResult:
    ok: bool
    wallet_state: str
    code: str
    message: str


@dataclass(frozen=True)
class WalletBalancesResult:
    ok: bool
    payload: dict[str, object] | None


class UnavailableWalletController:
    def open_public(self) -> WalletOpenResult:
        return WalletOpenResult(
            False,
            "",
            "WALLET_UNAVAILABLE",
            "Wallet is unavailable.",
        )

    def read_public_balances(self) -> WalletBalancesResult:
        return WalletBalancesResult(False, None)

    def open_or_activate(self, flow_id: str) -> WalletHandle:
        del flow_id
        raise RuntimeError("Wallet implementation is unavailable")

    def request_close(self, handle: WalletHandle) -> None:
        del handle
        raise RuntimeError("Wallet implementation is unavailable")


class SubprocessWalletController:
    def __init__(
        self,
        command: tuple[str, ...],
        close_callback: Callable[[WalletHandle], None] | None = None,
        activate_callback: Callable[[WalletHandle], None] | None = None,
    ) -> None:
        if not command:
            raise ValueError("Wallet command must not be empty")
        self._command = command
        self._close_callback = close_callback
        self._activate_callback = activate_callback
        self._current: WalletHandle | None = None

    def open_or_activate(self, flow_id: str) -> WalletHandle:
        if self._current is not None and self._current.poll() is None:
            if self._activate_callback is not None:
                self._activate_callback(self._current)
            return self._current
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        self._current = subprocess.Popen(
            [*self._command, flow_id], shell=False, close_fds=True, creationflags=creationflags
        )
        return self._current

    def read_public_balances(self) -> WalletBalancesResult:
        return WalletBalancesResult(False, None)

    def request_close(self, handle: WalletHandle) -> None:
        if self._close_callback is None:
            raise RuntimeError("Wallet close channel is unavailable")
        self._close_callback(handle)


class VerifiedWalletController(UnavailableWalletController):
    """Opens only one fixed executable and verifies its control-pipe peer."""

    def __init__(
        self,
        wallet_path: Path,
        control: WalletControlClient | None = None,
        process_factory: Callable[..., WalletHandle] = subprocess.Popen,
        readiness_timeout: float = 10.0,
        activation_timeout: float = 0.15,
        public_control: WalletPublicClient | None = None,
        public_response_timeout: float = 22.0,
    ) -> None:
        self._wallet_path = wallet_path.resolve(strict=False)
        self._control = control or WalletControlClient()
        self._public_control = public_control or WalletPublicClient()
        self._process_factory = process_factory
        self._readiness_timeout = readiness_timeout
        self._activation_timeout = activation_timeout
        self._public_response_timeout = public_response_timeout

    def open_public(self) -> WalletOpenResult:
        launch_id = str(uuid.uuid4())
        try:
            self._control.activate(
                launch_id,
                self._wallet_path,
                self._activation_timeout,
            )
            return WalletOpenResult(
                True,
                "ACTIVATED",
                "WALLET_ACTIVATED",
                "Wallet is open.",
            )
        except ControlUnavailable:
            pass
        except ControlProtocolError:
            return self._unavailable()

        if not self._wallet_path.is_file():
            return self._unavailable()
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        try:
            self._process_factory(
                [str(self._wallet_path)],
                shell=False,
                close_fds=True,
                creationflags=creationflags,
            )
        except Exception:
            return self._unavailable()
        try:
            self._control.activate(
                launch_id,
                self._wallet_path,
                self._readiness_timeout,
            )
        except (ControlProtocolError, ControlUnavailable):
            return self._unavailable()
        return WalletOpenResult(
            True,
            "OPENED",
            "WALLET_OPENED",
            "Wallet is open.",
        )

    def read_public_balances(self) -> WalletBalancesResult:
        if not self._wallet_path.is_file():
            return WalletBalancesResult(False, None)
        query_id = str(uuid.uuid4())
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        worker: WalletHandle | None = None
        try:
            worker = self._process_factory(
                [str(self._wallet_path), "--public-balances-worker"],
                shell=False,
                close_fds=True,
                creationflags=creationflags,
            )
            payload = self._public_control.read(
                query_id,
                self._wallet_path,
                self._readiness_timeout,
                self._public_response_timeout,
            )
            return WalletBalancesResult(True, payload)
        except Exception:
            if worker is not None and worker.poll() is None:
                terminate = getattr(worker, "terminate", None)
                if callable(terminate):
                    try:
                        terminate()
                    except Exception:
                        pass
            return WalletBalancesResult(False, None)

    @staticmethod
    def _unavailable() -> WalletOpenResult:
        return WalletOpenResult(
            False,
            "",
            "WALLET_UNAVAILABLE",
            "Wallet is unavailable.",
        )


class WindowsOwnerProbe:
    def is_alive(self, pid: int) -> bool:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(0x00101000, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == 0x00000102
        finally:
            kernel32.CloseHandle(handle)
