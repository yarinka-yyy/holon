"""Windows single-instance ownership and best-effort window activation."""

from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from typing import Protocol

ERROR_ALREADY_EXISTS = 183
SW_RESTORE = 9


class InstanceBackend(Protocol):
    def create(self, name: str) -> tuple[object, bool]: ...
    def activate(self, title: str) -> bool: ...
    def close(self, handle: object) -> None: ...


class WindowsInstanceBackend:
    def __init__(self) -> None:
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel32.CreateMutexW.restype = wintypes.HANDLE
        self.kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel32.CloseHandle.restype = wintypes.BOOL
        self.user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
        self.user32.FindWindowW.restype = wintypes.HWND
        self.user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        self.user32.SetForegroundWindow.argtypes = [wintypes.HWND]

    def create(self, name: str) -> tuple[object, bool]:
        ctypes.set_last_error(0)
        handle = self.kernel32.CreateMutexW(None, False, name)
        if not handle:
            raise ctypes.WinError(ctypes.get_last_error())
        return handle, ctypes.get_last_error() != ERROR_ALREADY_EXISTS

    def activate(self, title: str) -> bool:
        window = self.user32.FindWindowW(None, title)
        if not window:
            return False
        self.user32.ShowWindow(window, SW_RESTORE)
        return bool(self.user32.SetForegroundWindow(window))

    def close(self, handle: object) -> None:
        self.kernel32.CloseHandle(handle)


class ProcessInstance:
    def __init__(
        self, name: str, title: str, backend: InstanceBackend | None = None,
    ) -> None:
        self.name = name
        self.title = title
        self.backend = backend
        self.handle: object | None = None

    def acquire(self) -> bool:
        if self.backend is None:
            if sys.platform != "win32":
                self.handle = object()
                return True
            self.backend = WindowsInstanceBackend()
        handle, owned = self.backend.create(self.name)
        if owned:
            self.handle = handle
            return True
        self.backend.activate(self.title)
        self.backend.close(handle)
        return False

    def release(self) -> None:
        if self.handle is not None and self.backend is not None:
            self.backend.close(self.handle)
        self.handle = None
