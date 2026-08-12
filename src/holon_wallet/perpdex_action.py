"""Wallet-hosted sequential signing and submit-once Hyperliquid executor."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from eth_account import Account
from eth_account.messages import encode_typed_data

from .vault import AuthenticationFailedError, VaultRepository, VaultUnavailableError
from .wallet_crypto import InvalidSecretError, private_key_bytes, rederive

EXCHANGE_URL = "https://api.hyperliquid.xyz/exchange"
MAX_RESPONSE_BYTES = 1024 * 1024
HTTP_TIMEOUT_SECONDS = 12.0


class ExchangeRejected(RuntimeError):
    pass


class ExchangeOutcomeUnknown(RuntimeError):
    pass


class HttpExchangeTransport:
    """One HTTPS attempt. It deliberately contains no retry path."""

    def __call__(self, payload: Mapping[str, object]) -> object:
        raw = json.dumps(
            dict(payload), ensure_ascii=True, separators=(",", ":"), sort_keys=True,
        ).encode("utf-8")
        request = Request(
            EXCHANGE_URL, data=raw,
            headers={"Content-Type": "application/json", "User-Agent": "Holon/0.1"},
            method="POST",
        )
        try:
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                response_raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as exc:
            # A non-success HTTP response does not prove that the L1 action was
            # excluded. Hyperliquid semantic rejections arrive in a decoded
            # response and are handled during reconciliation instead.
            raise ExchangeOutcomeUnknown("Hyperliquid action outcome is unknown") from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise ExchangeOutcomeUnknown("Hyperliquid action outcome is unknown") from exc
        if not response_raw or len(response_raw) > MAX_RESPONSE_BYTES:
            raise ExchangeOutcomeUnknown("Hyperliquid action outcome is unknown")
        try:
            return json.loads(response_raw.decode("utf-8"), parse_float=str)
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise ExchangeOutcomeUnknown("Hyperliquid action outcome is unknown") from exc


class _TransientSigner:
    def __init__(self, key: bytearray) -> None:
        self._key = key
        self.address = Account.from_key(bytes(key)).address

    def sign_message(self, message):
        return Account.sign_message(message, private_key=bytes(self._key))


def _mainnet_l1_message(wire_digest: str):
    """Build the fixed Hyperliquid Agent message without importing its SDK.

    The optional module independently derives and verifies ``wire_digest`` via
    the pinned SDK. Wallet signs only this closed mainnet profile, so Base does
    not package the optional SDK and the module never receives a signer.
    """
    if (
        not isinstance(wire_digest, str)
        or len(wire_digest) != 64
        or any(character not in "0123456789abcdef" for character in wire_digest)
    ):
        raise RuntimeError("PERPDEX_WIRE_DIGEST_MISMATCH")
    return encode_typed_data(full_message={
        "domain": {
            "chainId": 1337,
            "name": "Exchange",
            "verifyingContract": "0x0000000000000000000000000000000000000000",
            "version": "1",
        },
        "types": {
            "Agent": [
                {"name": "source", "type": "string"},
                {"name": "connectionId", "type": "bytes32"},
            ],
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
        },
        "primaryType": "Agent",
        "message": {
            "source": "a",
            "connectionId": bytes.fromhex(wire_digest),
        },
    })


def _sign_mainnet_l1_digest(
    signer: _TransientSigner, wire_digest: str,
) -> tuple[dict[str, object], str]:
    message = _mainnet_l1_message(wire_digest)
    signed = signer.sign_message(message)
    signature = {"r": hex(signed.r), "s": hex(signed.s), "v": signed.v}
    recovered = Account.recover_message(
        message, vrs=[signature["v"], signature["r"], signature["s"]],
    )
    return signature, recovered


@dataclass(frozen=True, slots=True)
class PerpDexExecutionResult:
    operation_id: str
    action_type: str
    status: str
    code: str
    message: str
    phase_states: tuple[dict[str, object], ...]
    terminal_stage: str | None = None
    failure_category: str | None = None
    operation_class: str | None = None
    external_submission_started: bool = False

    def to_mapping(self) -> dict[str, object]:
        return {
            "operationId": self.operation_id,
            "actionType": self.action_type,
            "status": self.status,
            "code": self.code,
            "message": self.message,
            "phases": [dict(item) for item in self.phase_states],
            "terminalStage": self.terminal_stage,
            "failureCategory": self.failure_category,
            "operationClass": self.operation_class,
            "externalSubmissionStarted": self.external_submission_started,
        }


def _failure_category(code: str, submission_attempted: bool) -> str:
    if code == "AUTHENTICATION_FAILED":
        return "authentication"
    if code == "HYPERLIQUID_UNAVAILABLE":
        return "public_transport"
    if code.startswith("HYPERLIQUID_") and code not in {
        "HYPERLIQUID_ACTION_REJECTED", "HYPERLIQUID_RESULT_UNKNOWN",
    }:
        return "public_data"
    if code == "HYPERLIQUID_ACTION_REJECTED":
        return "exchange_rejected"
    if submission_attempted or code in {
        "HYPERLIQUID_RESULT_UNKNOWN", "PERPDEX_RESULT_UNKNOWN",
        "PERPDEX_RECONCILIATION_UNKNOWN", "PERPDEX_RECONCILIATION_INVALID",
    }:
        return "exchange_unknown"
    if code.startswith("PERPDEX_"):
        return "perpdex_state"
    return "internal"


class PerpDexExecutor:
    """Authenticates once, then signs each exact phase at most once in order."""

    def __init__(
        self, repository: VaultRepository, adapter: object,
        transport: Callable[[Mapping[str, object]], object] | None = None,
    ) -> None:
        self.repository = repository
        self.adapter = adapter
        self.transport = transport or HttpExchangeTransport()

    @staticmethod
    def _expires_ms(value: str) -> int:
        return int(datetime.fromisoformat(value.removesuffix("Z") + "+00:00").timestamp() * 1000)

    def execute(
        self, raw_bundle: Mapping[str, object], password: str,
        profile_id: str, account: Mapping[str, str],
    ) -> PerpDexExecutionResult:
        private_key: bytearray | None = None
        signer = None
        bundle = None
        phase_states: list[dict[str, object]] = []
        submission_attempted = False
        terminal_status = "FAILED"
        terminal_code = "PERPDEX_EXECUTION_FAILED"
        terminal_stage = "WALLET_EXECUTION_PRE_VERIFY"
        failure_category = "internal"
        operation_class = None
        terminal_message = "Nothing was automatically retried."
        operation_id = str(raw_bundle.get("operation_id", ""))
        try:
            bundle = self.adapter.verify(raw_bundle, account)
            self.adapter.mark_operation(bundle.operation_id, "EXECUTING")
            terminal_stage = "WALLET_AUTHENTICATION"
            record = self.repository._authenticate_profile(password, profile_id)
            if (
                record.summary.address.lower() != bundle.account.lower()
                or rederive(record.secret).lower() != bundle.account.lower()
            ):
                raise AuthenticationFailedError("Wallet profile changed")
            private_key = bytearray(private_key_bytes(record.secret))
            signer = _TransientSigner(private_key)
            if signer.address.lower() != bundle.account.lower():
                raise AuthenticationFailedError("Wallet signer changed")
            expires_after = self._expires_ms(bundle.expires_at)
            for index, phase in enumerate(bundle.phases):
                phase_submission_attempted = False
                terminal_stage = f"PHASE_{phase.phase_type.value}"
                try:
                    self.adapter.verify_phase(bundle, index, account)
                    action = dict(self.adapter.wire_action(phase))
                    verified_digest = self.adapter.wire_digest(phase)
                    if verified_digest != phase.wire_digest:
                        raise RuntimeError("PERPDEX_WIRE_DIGEST_MISMATCH")
                    self.adapter.mark_phase(
                        bundle.operation_id, phase.phase_id, "SUBMITTING",
                        code="SUBMITTING", public_id=phase.cloid,
                    )
                    signature, recovered = _sign_mainnet_l1_digest(
                        signer, verified_digest,
                    )
                    if recovered.lower() != bundle.account.lower():
                        raise RuntimeError("PERPDEX_SIGNER_MISMATCH")
                    # Persist this boundary before handing an exact signed
                    # payload to the external transport. A later interruption
                    # is never eligible for automatic history cleanup.
                    self.adapter.mark_external_submission_started(bundle.operation_id)
                    phase_submission_attempted = True
                    submission_attempted = True
                    response = self.transport({
                        "action": action,
                        "expiresAfter": expires_after,
                        "nonce": int(phase.nonce),
                        "signature": signature,
                        "vaultAddress": None,
                    })
                    del signature, action
                    terminal_stage = "RECONCILIATION"
                    try:
                        reconciled = dict(self.adapter.reconcile(phase, response, account))
                    except Exception as exc:
                        rejected = (
                            getattr(exc, "code", None)
                            == "HYPERLIQUID_ACTION_REJECTED"
                        )
                        reconciled = {
                            "state": "FAILED" if rejected else "UNKNOWN",
                            "code": (
                                "HYPERLIQUID_ACTION_REJECTED" if rejected
                                else "PERPDEX_RECONCILIATION_UNKNOWN"
                            ),
                            "public_id": phase.cloid,
                        }
                    del response
                    phase_state = str(reconciled.get("state"))
                    phase_code = str(reconciled.get("code"))
                    public_id = reconciled.get("public_id")
                    if phase_state not in {"CONFIRMED", "PARTIAL", "FAILED", "UNKNOWN"}:
                        phase_state, phase_code = "UNKNOWN", "PERPDEX_RECONCILIATION_INVALID"
                    self.adapter.mark_phase(
                        bundle.operation_id, phase.phase_id, phase_state,
                        code=phase_code,
                        public_id=(str(public_id) if public_id is not None else None),
                    )
                    phase_states.append({
                        "phaseId": phase.phase_id,
                        "phaseType": phase.phase_type.value,
                        "state": phase_state,
                        "code": phase_code,
                        "publicId": str(public_id) if public_id is not None else None,
                    })
                    if phase_state != "CONFIRMED":
                        terminal_status = phase_state
                        terminal_code = phase_code
                        break
                except ExchangeRejected:
                    self.adapter.mark_phase(
                        bundle.operation_id, phase.phase_id, "FAILED",
                        code="HYPERLIQUID_ACTION_REJECTED", public_id=phase.cloid,
                    )
                    phase_states.append({
                        "phaseId": phase.phase_id, "phaseType": phase.phase_type.value,
                        "state": "FAILED", "code": "HYPERLIQUID_ACTION_REJECTED",
                        "publicId": phase.cloid,
                    })
                    terminal_status, terminal_code = "FAILED", "HYPERLIQUID_ACTION_REJECTED"
                    break
                except ExchangeOutcomeUnknown:
                    try:
                        self.adapter.mark_phase(
                            bundle.operation_id, phase.phase_id, "UNKNOWN",
                            code="HYPERLIQUID_RESULT_UNKNOWN", public_id=phase.cloid,
                        )
                    except Exception:
                        pass
                    phase_states.append({
                        "phaseId": phase.phase_id, "phaseType": phase.phase_type.value,
                        "state": "UNKNOWN", "code": "HYPERLIQUID_RESULT_UNKNOWN",
                        "publicId": phase.cloid,
                    })
                    terminal_status, terminal_code = "UNKNOWN", "HYPERLIQUID_RESULT_UNKNOWN"
                    break
                except Exception as exc:
                    state = "UNKNOWN" if phase_submission_attempted else "FAILED"
                    code = str(getattr(exc, "code", None) or exc or "PERPDEX_PHASE_FAILED")
                    if not code.isupper() or len(code) > 64:
                        code = (
                            "PERPDEX_RESULT_UNKNOWN"
                            if phase_submission_attempted else "PERPDEX_PHASE_FAILED"
                        )
                    try:
                        self.adapter.mark_phase(
                            bundle.operation_id, phase.phase_id, state,
                            code=code, public_id=phase.cloid,
                        )
                    except Exception:
                        pass
                    phase_states.append({
                        "phaseId": phase.phase_id, "phaseType": phase.phase_type.value,
                        "state": state, "code": code, "publicId": phase.cloid,
                    })
                    terminal_status, terminal_code = state, code
                    break
            else:
                terminal_status = "COMPLETED"
                terminal_code = "PERPDEX_ACTION_COMPLETED"
                terminal_stage = "TERMINAL"
            operation_state = terminal_status
            failure_category = (
                None if terminal_status == "COMPLETED"
                else _failure_category(terminal_code, submission_attempted)
            )
            self.adapter.mark_operation(
                bundle.operation_id, operation_state, terminal_code=terminal_code,
                terminal_stage=terminal_stage, failure_category=failure_category,
                operation_class=operation_class,
            )
            terminal_message = (
                "Protected Hyperliquid action completed."
                if terminal_status == "COMPLETED"
                else "Execution stopped after the reported phase; no phase was retried."
            )
        except (AuthenticationFailedError, VaultUnavailableError, InvalidSecretError):
            terminal_status, terminal_code = "FAILED", "AUTHENTICATION_FAILED"
            terminal_stage = "WALLET_AUTHENTICATION"
            failure_category = "authentication"
            terminal_message = "Nothing was signed or sent."
            if operation_id:
                try:
                    self.adapter.mark_operation(
                        operation_id, terminal_status, terminal_code=terminal_code,
                        terminal_stage=terminal_stage, failure_category=failure_category,
                    )
                except Exception:
                    pass
        except Exception as exc:
            terminal_status = "UNKNOWN" if submission_attempted else "FAILED"
            terminal_code = str(getattr(exc, "code", None) or exc or "PERPDEX_EXECUTION_FAILED")
            if not terminal_code.isupper() or len(terminal_code) > 64:
                terminal_code = (
                    "PERPDEX_RESULT_UNKNOWN"
                    if submission_attempted else "PERPDEX_EXECUTION_FAILED"
                )
            failure_category = _failure_category(terminal_code, submission_attempted)
            requested_class = getattr(exc, "operation_class", None)
            operation_class = requested_class if isinstance(requested_class, str) else None
            terminal_message = "Nothing was automatically retried."
            if operation_id:
                try:
                    self.adapter.mark_operation(
                        operation_id, terminal_status, terminal_code=terminal_code,
                        terminal_stage=terminal_stage, failure_category=failure_category,
                        operation_class=operation_class,
                    )
                except Exception:
                    pass
        finally:
            if private_key is not None:
                for index in range(len(private_key)):
                    private_key[index] = 0
            del private_key, signer, password
        operation_id = (
            str(bundle.operation_id) if bundle is not None
            else operation_id
        )
        action_type = (
            bundle.intent.action_type.value if bundle is not None
            else str(raw_bundle.get("action_type", ""))
        )
        return PerpDexExecutionResult(
            operation_id, action_type, terminal_status, terminal_code,
            terminal_message, tuple(phase_states), terminal_stage,
            failure_category, operation_class, submission_attempted,
        )
