"""Bounded Windows named-pipe client used by the Hermes plugin."""

from __future__ import annotations

import ctypes
import os
import time
from multiprocessing.connection import Client

from holon_contracts import ContractEnvelope, MessageKind, make_envelope

from .codec import (
    MAX_MESSAGE_BYTES, PIPE_NAME, decode_message, encode_message, make_request,
    validate_response,
)
from .model import GuardHealth, GuardState


class PipeUnavailable(ConnectionError):
    pass


class PipeProtocolError(RuntimeError):
    """Safe classification for a bounded Guard pipe failure."""

    def __init__(self, message: str, code: str = "PROTOCOL_FAILED") -> None:
        super().__init__(message)
        self.code = code


WALLET_OPEN_RESPONSE_TIMEOUT = 15.0


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
        self, pipe_name: str = PIPE_NAME, connect_timeout: float = 0.5,
        response_timeout: float = 1.0,
    ) -> None:
        self.pipe_name = pipe_name
        self.connect_timeout = connect_timeout
        self.response_timeout = response_timeout

    def exchange(
        self, envelope: ContractEnvelope, owner_pid: int | None = None,
        response_timeout: float | None = None,
    ) -> ContractEnvelope:
        request = encode_message(make_request(envelope, owner_pid))
        wait_for_pipe(self.pipe_name, self.connect_timeout)
        try:
            connection = Client(self.pipe_name, family="AF_PIPE", authkey=None)
        except Exception as exc:
            raise PipeUnavailable("Guard pipe connection failed") from exc
        try:
            with connection:
                connection.send_bytes(request)
                if not connection.poll(
                    self.response_timeout if response_timeout is None else response_timeout,
                ):
                    raise PipeProtocolError("Guard response timed out", "RESPONSE_TIMEOUT")
                response = decode_message(connection.recv_bytes(MAX_MESSAGE_BYTES + 1))
        except PipeProtocolError:
            raise
        except Exception as exc:
            raise PipeProtocolError("Guard pipe response failed") from exc
        try:
            result = validate_response(response)
        except ValueError as exc:
            raise PipeProtocolError("Guard returned an invalid response") from exc
        if result.request_id != envelope.request_id or result.action_id != envelope.action_id:
            raise PipeProtocolError("Guard response correlation failed")
        return result

    def request(
        self, kind: MessageKind, payload: dict | None = None, *,
        action_id: str | None = None, owner_pid: int | None = None,
        response_timeout: float | None = None,
    ) -> ContractEnvelope:
        return self.exchange(
            make_envelope(kind, payload or {}, action_id=action_id),
            owner_pid,
            response_timeout,
        )


class PipeGuardClient:
    def __init__(self, client: PipeClient | None = None) -> None:
        self.client = client or PipeClient()

    def probe(self) -> GuardHealth:
        try:
            response = self.client.request(MessageKind.HEALTH_REQUEST)
            state = GuardState(response.payload["guard_state"])
            return GuardHealth.available(state)
        except PipeUnavailable:
            return GuardHealth.unavailable()
        except Exception:
            return GuardHealth.uncertain()

    def open_wallet(self) -> ContractEnvelope:
        # Guard may wait up to ten seconds for a newly spawned Wallet control pipe.
        return self.client.request(
            MessageKind.OPEN_WALLET, response_timeout=WALLET_OPEN_RESPONSE_TIMEOUT,
        )

    def wallet_balances(self) -> ContractEnvelope:
        return self.client.request(
            MessageKind.READ_WALLET_BALANCES,
            response_timeout=35.0,
        )

    def lending_markets(self, force_refresh: bool = False) -> ContractEnvelope:
        return self.client.request(
            MessageKind.READ_LENDING_MARKETS,
            {"force_refresh": force_refresh},
            response_timeout=20.0,
        )

    def lending_positions(self) -> ContractEnvelope:
        return self.client.request(
            MessageKind.READ_LENDING_POSITIONS,
            response_timeout=50.0,
        )

    def lending_portfolio(
        self, force_refresh: bool = False, history_period: str = "none",
    ) -> ContractEnvelope:
        return self.client.request(
            MessageKind.READ_LENDING_PORTFOLIO,
            {"force_refresh": force_refresh, "history_period": history_period},
            response_timeout=50.0,
        )

    def earn_portfolio(self, force_refresh: bool = False) -> ContractEnvelope:
        return self.client.request(
            MessageKind.READ_EARN_PORTFOLIO,
            {"force_refresh": force_refresh},
            response_timeout=50.0,
        )

    def module_read(
        self,
        module_id: str,
        capability_id: str,
        operation: str,
        params: dict[str, object],
    ) -> ContractEnvelope:
        return self.client.request(
            MessageKind.MODULE_READ_REQUEST,
            {
                "module_id": module_id,
                "capability_id": capability_id,
                "operation": operation,
                "params": params,
            },
            response_timeout=20.0,
        )

    def module_action_preview(
        self,
        module_id: str,
        capability_id: str,
        action_type: str,
        params: dict[str, object],
    ) -> ContractEnvelope:
        return self.client.request(
            MessageKind.MODULE_ACTION_INTENT,
            {
                "module_id": module_id,
                "capability_id": capability_id,
                "action_type": action_type,
                "params": params,
            },
            response_timeout=40.0,
        )

    def module_action_execute(
        self,
        module_id: str,
        capability_id: str,
        action_type: str,
        params: dict[str, object],
        preview_digest: str,
        action_id: str,
    ) -> ContractEnvelope:
        return self.client.request(
            MessageKind.MODULE_AUTHORITY_INTENT,
            {
                "module_id": module_id,
                "capability_id": capability_id,
                "action_type": action_type,
                "params": params,
                "preview_digest": preview_digest,
            },
            action_id=action_id,
            owner_pid=os.getpid(),
            response_timeout=40.0,
        )

    def module_action_status(
        self, action_id: str, module_id: str = "holon.perpdex",
        capability_id: str = "holon.perpdex.action.guard",
    ) -> ContractEnvelope:
        return self.client.request(
            MessageKind.MODULE_ACTION_STATUS_REQUEST,
            {"module_id": module_id, "capability_id": capability_id},
            action_id=action_id,
        )

    def lending_action_preview(
        self, payload: dict[str, object],
    ) -> ContractEnvelope:
        return self.client.request(
            MessageKind.LENDING_ACTION_INTENT,
            payload,
            response_timeout=40.0,
        )

    def lending_action_execute(
        self, payload: dict[str, object], action_id: str,
    ) -> ContractEnvelope:
        return self.client.request(
            MessageKind.LENDING_AUTHORITY_INTENT, payload,
            action_id=action_id, owner_pid=os.getpid(), response_timeout=40.0,
        )

    def prepare_transfer(
        self, payload: dict[str, str], action_id: str,
    ) -> ContractEnvelope:
        return self.client.request(
            MessageKind.TRANSFER_INTENT,
            payload,
            action_id=action_id,
            owner_pid=os.getpid(),
            response_timeout=35.0,
        )

    def transfer_status(self, action_id: str) -> ContractEnvelope:
        return self.client.request(
            MessageKind.ACTION_STATUS_REQUEST, action_id=action_id,
        )

    def cancel_transfer(self, action_id: str) -> ContractEnvelope:
        return self.client.request(MessageKind.CANCEL_ACTION, action_id=action_id)

    def recover_transfer(self, action_id: str) -> ContractEnvelope:
        return self.client.request(MessageKind.RECOVER_ACTION, action_id=action_id)
