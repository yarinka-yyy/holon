"""Wallet process seam; M2.02 uses only injected mock implementations."""

from __future__ import annotations

import ctypes
import subprocess
import sys
from ctypes import wintypes
from typing import Callable, Protocol


class WalletHandle(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...


class WalletController(Protocol):
    def open_or_activate(self, flow_id: str) -> WalletHandle: ...

    def request_close(self, handle: WalletHandle) -> None: ...


class OwnerProbe(Protocol):
    def is_alive(self, pid: int) -> bool: ...


class UnavailableWalletController:
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

    def request_close(self, handle: WalletHandle) -> None:
        if self._close_callback is None:
            raise RuntimeError("Wallet close channel is unavailable")
        self._close_callback(handle)


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
