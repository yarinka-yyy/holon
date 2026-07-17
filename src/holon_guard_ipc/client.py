"""Bounded Windows named-pipe client used by the Hermes plugin."""

from __future__ import annotations

import ctypes
import time
from multiprocessing.connection import Client
from typing import Any, Mapping

from .codec import (
    MAX_MESSAGE_BYTES,
    PIPE_NAME,
    decode_message,
    encode_message,
    make_request,
    validate_response,
)
from .model import GuardHealth, GuardState


class PipeUnavailable(ConnectionError):
    pass


class PipeProtocolError(RuntimeError):
    pass


def wait_for_pipe(pipe_name: str, timeout: float) -> None:
    wait_named_pipe = ctypes.WinDLL("kernel32", use_last_error=True).WaitNamedPipeW
    wait_named_pipe.argtypes = [ctypes.c_wchar_p, ctypes.c_uint32]
    wait_named_pipe.restype = ctypes.c_int
    deadline = time.monotonic() + max(timeout, 0.0)
    while True:
        remaining_ms = max(1, min(100, int((deadline - time.monotonic()) * 1000)))
        if wait_named_pipe(pipe_name, remaining_ms):
            return
        if time.monotonic() >= deadline:
            raise PipeUnavailable("Guard pipe is unavailable")
        time.sleep(0.05)


class PipeClient:
    def __init__(
        self,
        pipe_name: str = PIPE_NAME,
        connect_timeout: float = 0.5,
        response_timeout: float = 1.0,
    ) -> None:
        self.pipe_name = pipe_name
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout

    def command(self, command: str, payload: Mapping[str, Any] | None = None) -> dict[str, Any]:
        request = encode_message(make_request(command, payload))
        wait_for_pipe(self.pipe_name, self.connect_timeout)
        try:
            connection = Client(self.pipe_name, family="AF_PIPE", authkey=None)
        except Exception as exc:
            raise PipeUnavailable("Guard pipe connection failed") from exc
        try:
            with connection:
                connection.send_bytes(request)
                if not connection.poll(self.response_timeout):
                    raise PipeProtocolError("Guard response timed out")
                response = decode_message(connection.recv_bytes(MAX_MESSAGE_BYTES + 1))
        except PipeProtocolError:
            raise
        except Exception as exc:
            raise PipeProtocolError("Guard pipe response failed") from exc
        try:
            validate_response(response)
        except ValueError as exc:
            raise PipeProtocolError("Guard returned an invalid response") from exc
        return response


class PipeGuardClient:
    def __init__(self, client: PipeClient | None = None) -> None:
        self.client = client or PipeClient()

    def probe(self) -> GuardHealth:
        try:
            response = self.client.command("health")
            if not response["ok"]:
                return GuardHealth.uncertain()
            return GuardHealth.available(GuardState(response["state"]))
        except PipeUnavailable:
            return GuardHealth.unavailable()
        except Exception:
            return GuardHealth.uncertain()
