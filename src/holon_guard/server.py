"""Windows named-pipe server for the local Guard lifecycle."""

from __future__ import annotations

import queue
import threading
from multiprocessing.connection import Connection, Listener
from typing import Any

from holon_guard_ipc.codec import (
    MAX_MESSAGE_BYTES,
    decode_message,
    encode_message,
    make_response,
    validate_request,
)

from .lifecycle import GuardLifecycle
from .model import GuardResult

MONITOR_INTERVAL = 0.25


class GuardServer:
    def __init__(
        self,
        pipe_name: str,
        lifecycle: GuardLifecycle,
        monitor_interval: float = MONITOR_INTERVAL,
    ) -> None:
        self.pipe_name = pipe_name
        self.lifecycle = lifecycle
        self.monitor_interval = monitor_interval
        self._stop = threading.Event()
        self._listener: Listener | None = None
        self._connections: queue.Queue[Connection] = queue.Queue()

    def _dispatch(self, request: dict[str, Any]) -> GuardResult:
        command = request["command"]
        payload = request["payload"]
        if command == "health":
            return self.lifecycle.health()
        if command == "start_flow":
            return self.lifecycle.start_flow(payload["owner_pid"])
        if command == "cancel_flow":
            return self.lifecycle.cancel_flow(payload["flow_id"])
        return self.lifecycle.recover_flow(payload["flow_id"])

    def _safe_response(self, result: GuardResult) -> bytes:
        return encode_message(
            make_response(
                ok=result.ok,
                code=result.code,
                state=result.state,
                message=result.message,
                flow_id=result.flow_id,
            )
        )

    def _handle_connection(self, connection: Connection) -> None:
        try:
            try:
                if not connection.poll(self.monitor_interval):
                    raise TimeoutError("Guard request timed out")
                raw = connection.recv_bytes(MAX_MESSAGE_BYTES + 1)
                request = decode_message(raw)
                validate_request(request)
                response = self._safe_response(self._dispatch(request))
            except Exception:
                result = GuardResult(
                    False,
                    "IPC_INVALID_REQUEST",
                    self.lifecycle.snapshot.state,
                    "Guard rejected an invalid request.",
                    self.lifecycle.snapshot.flow_id,
                )
                response = self._safe_response(result)
            connection.send_bytes(response)
        except Exception:
            return
        finally:
            connection.close()

    def _accept_loop(self) -> None:
        assert self._listener is not None
        while not self._stop.is_set():
            try:
                self._connections.put(self._listener.accept())
            except (OSError, EOFError):
                return

    def serve_forever(self) -> None:
        self._listener = Listener(self.pipe_name, family="AF_PIPE", authkey=None)
        accept_thread = threading.Thread(target=self._accept_loop, daemon=True)
        accept_thread.start()
        try:
            while not self._stop.is_set():
                try:
                    connection = self._connections.get(timeout=self.monitor_interval)
                except queue.Empty:
                    connection = None
                if connection is not None:
                    self._handle_connection(connection)
                self.lifecycle.monitor_once()
        finally:
            self._stop.set()
            self._listener.close()
            accept_thread.join(timeout=1.0)

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
