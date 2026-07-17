"""Windows named-pipe server for versioned Guard contracts."""

from __future__ import annotations

import queue
import threading
from multiprocessing.connection import Connection, Listener

from holon_contracts import ContractViolation, SecurityCode
from holon_journal import EventType
from holon_guard_ipc.codec import (
    MAX_MESSAGE_BYTES, decode_message, encode_message, make_response, validate_request,
)

from .authority import AuthorityService
from .server_responses import contract_failure, generic_error

MONITOR_INTERVAL = 0.25


class GuardServer:
    def __init__(
        self, pipe_name: str, authority: AuthorityService,
        monitor_interval: float = MONITOR_INTERVAL,
    ) -> None:
        self.pipe_name = pipe_name
        self.authority = authority
        self.monitor_interval = monitor_interval
        self._stop = threading.Event()
        self._listener: Listener | None = None
        self._connections: queue.Queue[Connection] = queue.Queue()

    def _handle_connection(self, connection: Connection) -> None:
        frame: object = None
        try:
            if not connection.poll(self.monitor_interval):
                raise TimeoutError("Guard request timed out")
            frame = decode_message(connection.recv_bytes(MAX_MESSAGE_BYTES + 1))
            request, owner_pid = validate_request(frame)
            try:
                response = self.authority.handle(request, owner_pid)
            except Exception:
                self.authority.audit_system(
                    EventType.TECHNICAL_ERROR, SecurityCode.IPC_INVALID_REQUEST.value
                )
                response = self.authority.error(
                    request, SecurityCode.IPC_INVALID_REQUEST.value,
                    "Guard could not process the request.",
                )
            encoded = encode_message(make_response(response))
            connection.send_bytes(encoded)
        except ContractViolation as violation:
            self.authority.audit_system(EventType.REFUSAL, violation.code)
            try:
                connection.send_bytes(contract_failure(frame, violation))
            except Exception:
                try:
                    connection.send_bytes(generic_error())
                except Exception:
                    return
        except Exception:
            self.authority.audit_system(
                EventType.TECHNICAL_ERROR, SecurityCode.IPC_INVALID_REQUEST.value
            )
            try:
                connection.send_bytes(generic_error())
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
                snapshot = self.authority.lifecycle.snapshot
                result = self.authority.lifecycle.monitor_once()
                if result.state is not snapshot.state or result.code not in {"OK", snapshot.reason}:
                    self.authority.audit_monitor(result, snapshot.action_id, snapshot.flow_id)
        finally:
            self._stop.set()
            self._listener.close()
            accept_thread.join(timeout=1.0)

    def stop(self) -> None:
        self._stop.set()
        if self._listener is not None:
            self._listener.close()
