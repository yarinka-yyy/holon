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
    WalletAuthorityClient,
    WalletControlClient,
    WalletLendingPreviewClient,
    WalletPublicClient,
)

WALLET_INITIALIZATION_FAILED_EXIT_CODE = 20
WALLET_INSTANCE_UNREACHABLE_EXIT_CODE = 21
WALLET_OPEN_FAILURE_MESSAGES = {
    "WALLET_EXECUTABLE_MISSING": "Wallet executable is missing.",
    "WALLET_START_FAILED": "Wallet could not be started.",
    "WALLET_INITIALIZATION_FAILED": "Wallet could not initialize.",
    "WALLET_EXITED": "Wallet exited before it became ready.",
    "WALLET_INSTANCE_UNREACHABLE": "The existing Wallet instance could not be reached.",
    "WALLET_STARTUP_TIMEOUT": "Wallet did not become ready in time.",
    "CONTROL_PROTOCOL_FAILED": "Wallet control protocol failed.",
    "WALLET_PROCESS_VERIFICATION_FAILED": "Wallet process verification failed.",
    "WALLET_OPEN_INTERNAL_FAILURE": "Wallet launch failed inside Guard.",
}


class WalletHandle(Protocol):
    @property
    def pid(self) -> int: ...

    def poll(self) -> int | None: ...


class WalletController(Protocol):
    def open_public(self) -> "WalletOpenResult": ...

    def read_public_balances(self) -> "WalletBalancesResult": ...

    def open_or_activate(self, flow_id: str) -> WalletHandle: ...

    def request_close(self, handle: WalletHandle) -> None: ...

    def prepare_transfer(self, request: dict[str, object]) -> "WalletPreparedResult": ...

    def prepare_lending_action(self, request: dict[str, object]) -> "WalletPreparedResult": ...

    def prepare_module_action(self, request: dict[str, object]) -> "WalletPreparedResult": ...

    def preview_lending(
        self, intent: dict[str, object], profile_digest: str,
    ) -> "WalletLendingPreviewResult": ...

    def cancel_transfer(self, request: dict[str, object]) -> bool: ...


class OwnerProbe(Protocol):
    def is_alive(self, pid: int) -> bool: ...


@dataclass(frozen=True)
class WalletOpenResult:
    ok: bool
    wallet_state: str
    code: str
    message: str
    exit_code: int | None = None


def wallet_open_failure(code: str, exit_code: int | None = None) -> WalletOpenResult:
    if code not in WALLET_OPEN_FAILURE_MESSAGES:
        code = "WALLET_UNAVAILABLE"
    if code == "WALLET_UNAVAILABLE":
        message = "Wallet is unavailable."
    else:
        message = WALLET_OPEN_FAILURE_MESSAGES[code]
    if code == "WALLET_EXITED" and type(exit_code) is int:
        message = f"Wallet exited before it became ready (exit code {exit_code})."
    return WalletOpenResult(False, "", code, message, exit_code)


@dataclass(frozen=True)
class WalletBalancesResult:
    ok: bool
    payload: dict[str, object] | None


@dataclass(frozen=True)
class WalletPreparedResult:
    ok: bool
    code: str
    payload: dict[str, object] | None
    handle: WalletHandle | None


@dataclass(frozen=True)
class WalletLendingPreviewResult:
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

    def prepare_transfer(self, request: dict[str, object]) -> WalletPreparedResult:
        del request
        return WalletPreparedResult(False, "WALLET_UNAVAILABLE", None, None)

    def prepare_lending_action(self, request: dict[str, object]) -> WalletPreparedResult:
        return self.prepare_transfer(request)

    def prepare_module_action(self, request: dict[str, object]) -> WalletPreparedResult:
        return self.prepare_transfer(request)

    def preview_lending(
        self, intent: dict[str, object], profile_digest: str,
    ) -> WalletLendingPreviewResult:
        del intent, profile_digest
        return WalletLendingPreviewResult(False, None)

    def cancel_transfer(self, request: dict[str, object]) -> bool:
        del request
        return False


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
        authority_control: WalletAuthorityClient | None = None,
        authority_timeout: float = 70.0,
        lending_preview_control: WalletLendingPreviewClient | None = None,
        lending_preview_timeout: float = 30.0,
    ) -> None:
        self._wallet_path = wallet_path.resolve(strict=False)
        self._control = control or WalletControlClient()
        self._public_control = public_control or WalletPublicClient()
        self._process_factory = process_factory
        self._readiness_timeout = readiness_timeout
        self._activation_timeout = activation_timeout
        self._public_response_timeout = public_response_timeout
        self._authority_control = authority_control or WalletAuthorityClient()
        self._authority_timeout = authority_timeout
        self._lending_preview_control = lending_preview_control or WalletLendingPreviewClient()
        self._lending_preview_timeout = lending_preview_timeout
        self._current: WalletHandle | None = None

    @property
    def wallet_path(self) -> Path:
        return self._wallet_path

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
                "Wallet activation was requested.",
            )
        except ControlUnavailable:
            pass
        except ControlProtocolError as error:
            return wallet_open_failure(error.code)

        if not self._wallet_path.is_file():
            return wallet_open_failure("WALLET_EXECUTABLE_MISSING")
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        try:
            self._current = self._process_factory(
                [str(self._wallet_path)],
                shell=False,
                close_fds=True,
                creationflags=creationflags,
            )
        except Exception:
            return wallet_open_failure("WALLET_START_FAILED")
        try:
            self._control.activate(
                launch_id,
                self._wallet_path,
                self._readiness_timeout,
            )
        except ControlProtocolError as error:
            return wallet_open_failure(error.code)
        except ControlUnavailable:
            exit_code = self._safe_exit_code(self._current)
            if exit_code in {0, WALLET_INSTANCE_UNREACHABLE_EXIT_CODE}:
                return wallet_open_failure("WALLET_INSTANCE_UNREACHABLE")
            if exit_code == WALLET_INITIALIZATION_FAILED_EXIT_CODE:
                return wallet_open_failure("WALLET_INITIALIZATION_FAILED")
            if exit_code is not None:
                return wallet_open_failure("WALLET_EXITED", exit_code)
            return wallet_open_failure("WALLET_STARTUP_TIMEOUT")
        return WalletOpenResult(
            True,
            "OPENED",
            "WALLET_OPENED",
            "Wallet launch was verified.",
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

    def prepare_transfer(self, request: dict[str, object]) -> WalletPreparedResult:
        response: dict[str, object] | None = None
        try:
            response = self._authority_control.exchange(
                request, self._wallet_path, self._activation_timeout,
                self._authority_timeout,
            )
        except ControlUnavailable:
            if not self._wallet_path.is_file():
                return WalletPreparedResult(False, "WALLET_UNAVAILABLE", None, None)
            creationflags = 0x08000000 if sys.platform == "win32" else 0
            try:
                self._process_factory(
                    [str(self._wallet_path)], shell=False, close_fds=True,
                    creationflags=creationflags,
                )
                response = self._authority_control.exchange(
                    request, self._wallet_path, self._readiness_timeout,
                    self._authority_timeout,
                )
            except ControlProtocolError:
                return WalletPreparedResult(
                    False, "WALLET_PREPARATION_AMBIGUOUS", None, None,
                )
            except Exception:
                return WalletPreparedResult(False, "WALLET_UNAVAILABLE", None, None)
        except ControlProtocolError:
            return WalletPreparedResult(
                False, "WALLET_PREPARATION_AMBIGUOUS", None, None,
            )
        except Exception:
            return WalletPreparedResult(False, "WALLET_UNAVAILABLE", None, None)
        if response is None:
            return WalletPreparedResult(False, "WALLET_UNAVAILABLE", None, None)
        pid = response.get("wallet_pid")
        if type(pid) is not int:
            return WalletPreparedResult(False, "WALLET_UNAVAILABLE", None, None)
        handle = WindowsProcessReference(pid)
        if response.get("kind") not in {
            "transfer_prepared", "lending_action_prepared", "module_action_prepared",
        }:
            return WalletPreparedResult(
                False, str(response.get("code", "TRANSFER_PREPARATION_FAILED")),
                response, handle,
            )
        return WalletPreparedResult(True, str(response["code"]), response, handle)

    def prepare_lending_action(self, request: dict[str, object]) -> WalletPreparedResult:
        return self.prepare_transfer(request)

    def prepare_module_action(self, request: dict[str, object]) -> WalletPreparedResult:
        return self.prepare_transfer(request)

    def preview_lending(
        self, intent: dict[str, object], profile_digest: str,
    ) -> WalletLendingPreviewResult:
        if not self._wallet_path.is_file():
            return WalletLendingPreviewResult(False, None)
        request = {
            "preview_version": "1", "kind": "prepare_lending_preview",
            "correlation_id": str(uuid.uuid4()), "profile_digest": profile_digest,
            "intent": dict(intent),
        }
        creationflags = 0x08000000 if sys.platform == "win32" else 0
        worker: WalletHandle | None = None
        try:
            worker = self._process_factory(
                [str(self._wallet_path), "--lending-preview-worker"],
                shell=False, close_fds=True, creationflags=creationflags,
            )
            response = self._lending_preview_control.prepare(
                request, self._wallet_path, self._readiness_timeout,
                self._lending_preview_timeout,
            )
            preview = response.get("preview")
            if not isinstance(preview, dict):
                raise ControlProtocolError("Wallet preview is invalid")
            return WalletLendingPreviewResult(True, preview)
        except Exception:
            if worker is not None and worker.poll() is None:
                terminate = getattr(worker, "terminate", None)
                if callable(terminate):
                    try:
                        terminate()
                    except Exception:
                        pass
            return WalletLendingPreviewResult(False, None)

    def cancel_transfer(self, request: dict[str, object]) -> bool:
        try:
            response = self._authority_control.exchange(
                request, self._wallet_path, self._activation_timeout, 2.0,
            )
        except Exception:
            return False
        return response.get("kind") in {"transfer_cancelled", "action_cancelled"}

    @staticmethod
    def _safe_exit_code(handle: WalletHandle | None) -> int | None:
        if handle is None:
            return None
        try:
            value = handle.poll()
        except Exception:
            return None
        return value if type(value) is int else None


class WindowsProcessReference:
    def __init__(self, pid: int) -> None:
        self.pid = pid

    def poll(self) -> int | None:
        return None if WindowsOwnerProbe().is_alive(self.pid) else 1


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
