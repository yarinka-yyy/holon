"""Atomic secret-free persistence for PerpDEX nonces and operation results."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
import json
import os
import re
import tempfile
import threading
import time
import uuid

from .contracts import (
    ActionType, ContractError, PerpDexActionIntent, PhaseType,
    ProtectedActionBundle, canonical_json,
)

NONCE_VERSION = "1"
OPERATIONS_VERSION = "3"
PREVIOUS_OPERATIONS_VERSION = "2"
LEGACY_OPERATIONS_VERSION = "1"
MAX_FILE_BYTES = 1024 * 1024
MAX_OPERATIONS = 128
STALE_OPERATION_SECONDS = 301
TRANSIENT_OPERATION_SECONDS = 30 * 24 * 60 * 60
PHASE_STATES = frozenset({
    "PENDING", "SUBMITTING", "CONFIRMED", "FAILED", "PARTIAL", "UNKNOWN",
    "PENDING_CREDIT",
})
OPERATION_STATES = frozenset({
    "PREPARED", "AWAITING_LOCAL_CONFIRMATION", "EXECUTING", "COMPLETED",
    "FAILED", "PARTIAL", "UNKNOWN", "PENDING_CREDIT", "REJECTED", "EXPIRED",
})
TERMINAL_STATES = frozenset({
    "COMPLETED", "FAILED", "PARTIAL", "UNKNOWN", "PENDING_CREDIT", "REJECTED", "EXPIRED",
})
OPERATION_TRANSITIONS = {
    "PREPARED": frozenset({
        "AWAITING_LOCAL_CONFIRMATION", "FAILED", "REJECTED", "EXPIRED",
    }),
    "AWAITING_LOCAL_CONFIRMATION": frozenset({
        "EXECUTING", "FAILED", "REJECTED", "EXPIRED",
    }),
    "EXECUTING": frozenset({"COMPLETED", "FAILED", "PARTIAL", "UNKNOWN", "PENDING_CREDIT"}),
}
PHASE_TRANSITIONS = {
    "PENDING": frozenset({"SUBMITTING"}),
    "SUBMITTING": frozenset({"CONFIRMED", "FAILED", "PARTIAL", "UNKNOWN", "PENDING_CREDIT"}),
}
_ACTION_RE = re.compile(
    r"^act-[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$"
)
_ADDRESS_RE = re.compile(r"^0x[0-9a-f]{40}$")
_PHASE_RE = re.compile(r"^phase-[0-9a-f]{32}$")
_CLOID_RE = re.compile(r"^0x[0-9a-f]{32}$")
_DIGEST_RE = re.compile(r"^[0-9a-f]{64}$")
_CODE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
TERMINAL_STAGES = frozenset({
    "WALLET_LIVE_VERIFY", "WALLET_EXECUTION_PRE_VERIFY", "WALLET_AUTHENTICATION",
    "PHASE_SET_ISOLATED_LEVERAGE", "PHASE_CANCEL_MARKET_ORDERS",
    "PHASE_SET_REFERRER", "PHASE_PLACE_IOC_ORDER", "PHASE_VAULT_TRANSFER",
    "RECONCILIATION", "TERMINAL",
})
FAILURE_CATEGORIES = frozenset({
    "authentication", "cancelled", "exchange_rejected", "exchange_unknown",
    "expired", "internal", "perpdex_state", "public_data", "public_transport",
    "wallet", "wallet_ipc",
})
OPERATION_CLASSES = frozenset({
    "clearinghouseState", "frontendOpenOrders", "l2Book", "metaAndAssetCtxs",
    "orderStatus", "referral", "userFees", "userFillsByTime",
    "userNonFundingLedgerUpdates", "userVaultEquities", "vaultDetails",
})


def _legacy_terminal_diagnostics(operation: Mapping[str, object]) -> dict[str, object | None]:
    """Migrate v1/v2 without inventing a specific failure that was never stored."""
    state = str(operation.get("state", ""))
    phase = next((
        item for item in operation.get("phases", [])
        if isinstance(item, Mapping)
        and item.get("state") in {"CONFIRMED", "FAILED", "PARTIAL", "UNKNOWN", "PENDING_CREDIT"}
        and isinstance(item.get("code"), str)
    ), None)
    phase_type = str(phase.get("phase_type", "")) if isinstance(phase, Mapping) else ""
    code = str(phase["code"]) if isinstance(phase, Mapping) else {
        "COMPLETED": "PERPDEX_ACTION_COMPLETED",
        "PARTIAL": "IOC_PARTIAL_FILL",
        "UNKNOWN": "PERPDEX_RESULT_UNKNOWN",
        "PENDING_CREDIT": "HYPERLIQUID_CREDIT_PENDING",
        "REJECTED": "ACTION_CANCELLED",
        "EXPIRED": "ACTION_EXPIRED",
    }.get(state)
    stage = f"PHASE_{phase_type}" if phase_type else (
        "RECONCILIATION" if state in {"COMPLETED", "PARTIAL", "UNKNOWN", "PENDING_CREDIT"}
        else "TERMINAL" if code is not None else None
    )
    category = (
        "cancelled" if state == "REJECTED"
        else "expired" if state == "EXPIRED"
        else "exchange_unknown" if state in {"PARTIAL", "UNKNOWN", "PENDING_CREDIT"}
        else None
    )
    return {
        "failure_category": category,
        "operation_class": None,
        "terminal_code": code,
        "terminal_stage": stage,
    }


class PersistenceError(RuntimeError):
    """Bounded local-state failure with a safe machine-readable category."""

    def __init__(
        self, message: str, code: str = "PERPDEX_PERSISTENCE_INVALID",
    ) -> None:
        super().__init__(message)
        self.code = code


def _legacy_submission_started(operation: Mapping[str, object]) -> bool:
    """Preserve v1 records unless their local-only outcome is provable."""
    state = str(operation.get("state", ""))
    phases = operation.get("phases")
    if state in {"COMPLETED", "PARTIAL", "UNKNOWN", "PENDING_CREDIT"}:
        return True
    if isinstance(phases, list) and any(
        isinstance(phase, Mapping)
        and (
            phase.get("state") in {"CONFIRMED", "PARTIAL", "UNKNOWN", "PENDING_CREDIT"}
            or phase.get("code") in {
                "HYPERLIQUID_ACTION_REJECTED", "IOC_ORDER_REJECTED", "IOC_NOT_FILLED",
            }
        )
        for phase in phases
    ):
        return True
    if state in {"REJECTED", "EXPIRED"}:
        return False
    # Old funding rows have a narrow set of known pre-sign stops. Everything
    # else is deliberately retained because v1 did not record this boundary.
    safe_funding_codes = {
        "FUNDING_REVALIDATION_FAILED", "FUNDING_POLICY_UNAVAILABLE",
        "FUNDING_AUTHENTICATION_FAILED", "FUNDING_ACTION_EXPIRED",
        "FUNDING_ACTION_INVALID", "FUNDING_CANCELLED", "FUNDING_SIGNING_FAILED",
        "FUNDING_FEE_LIMIT_EXCEEDED", "FUNDING_GUARD_FEE_CAP_EXCEEDED",
        "FUNDING_WALLET_FEE_CAP_EXCEEDED", "FUNDING_ACCOUNT_CHANGED",
        "FUNDING_AMOUNT_CHANGED", "FUNDING_WALLET_ROUTE_CHANGED",
    }
    if (
        state == "FAILED"
        and operation.get("action_type") == "FUND_TRADING_ACCOUNT"
        and isinstance(phases, list)
        and all(
            isinstance(phase, Mapping)
            and phase.get("public_id") is None
            and phase.get("code") in safe_funding_codes
            for phase in phases
        )
    ):
        return False
    return True


def _read(path: Path) -> object:
    try:
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise PersistenceError(
            "PerpDEX state is unavailable", "PERPDEX_PERSISTENCE_UNAVAILABLE",
        ) from exc
    if not raw or len(raw) > MAX_FILE_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        raise PersistenceError("PerpDEX state is invalid")
    try:
        return json.loads(raw.decode("utf-8"), parse_float=lambda _value: (_ for _ in ()).throw(ValueError()))
    except (UnicodeError, ValueError, json.JSONDecodeError) as exc:
        raise PersistenceError("PerpDEX state is invalid") from exc


def _atomic_write(path: Path, value: object) -> None:
    raw = canonical_json(value)
    if len(raw) > MAX_FILE_BYTES:
        raise PersistenceError("PerpDEX state exceeds its bound")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(raw)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    except Exception as exc:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise PersistenceError(
            "PerpDEX state is unavailable", "PERPDEX_PERSISTENCE_UNAVAILABLE",
        ) from exc


class PerpDexNonceStore:
    def __init__(self, path: Path, clock_ms=None) -> None:
        self.path = path
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()

    def allocate(self, count: int) -> tuple[str, ...]:
        if type(count) is not int or not 1 <= count <= 8:
            raise PersistenceError("Invalid PerpDEX nonce allocation")
        with self._lock:
            value = _read(self.path)
            previous = 0
            if value is not None:
                if (
                    not isinstance(value, Mapping)
                    or set(value) != {"last_nonce", "nonce_version"}
                    or value.get("nonce_version") != NONCE_VERSION
                    or not isinstance(value.get("last_nonce"), str)
                    or not value["last_nonce"].isdigit()
                    or len(value["last_nonce"]) > 20
                ):
                    raise PersistenceError("PerpDEX nonce state is invalid")
                previous = int(value["last_nonce"])
            first = max(previous + 1, int(self._clock_ms()))
            last = first + count - 1
            if first <= 0 or last >= 2**64:
                raise PersistenceError("PerpDEX nonce is out of range")
            _atomic_write(self.path, {
                "last_nonce": str(last), "nonce_version": NONCE_VERSION,
            })
            return tuple(str(first + offset) for offset in range(count))


class PerpDexOperationStore:
    def __init__(self, path: Path, clock=None) -> None:
        self.path = path
        self._clock = clock or time.time
        self._lock = threading.RLock()

    @staticmethod
    def _timestamp(now: float) -> str:
        return datetime.fromtimestamp(now, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")

    @staticmethod
    def _validate_timestamp(value: object) -> None:
        if not isinstance(value, str) or len(value) > 40 or not value.endswith("Z"):
            raise PersistenceError("PerpDEX operation is invalid")
        try:
            parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
        except ValueError as exc:
            raise PersistenceError("PerpDEX operation is invalid") from exc
        if parsed.tzinfo != UTC:
            raise PersistenceError("PerpDEX operation is invalid")

    def _load(self) -> list[dict[str, object]]:
        value = _read(self.path)
        if value is None:
            return []
        if (
            not isinstance(value, Mapping)
            or set(value) != {"operations", "operations_version"}
            or value.get("operations_version") not in {
                LEGACY_OPERATIONS_VERSION, PREVIOUS_OPERATIONS_VERSION,
                OPERATIONS_VERSION,
            }
            or not isinstance(value.get("operations"), list)
            or len(value["operations"]) > MAX_OPERATIONS
        ):
            raise PersistenceError("PerpDEX operation state is invalid")
        operations: list[dict[str, object]] = []
        seen: set[str] = set()
        version = str(value["operations_version"])
        for raw in value["operations"]:
            operation = self._validate_operation(raw, version=version)
            operation_id = str(operation["operation_id"])
            if operation_id in seen:
                raise PersistenceError("Duplicate PerpDEX operation")
            seen.add(operation_id)
            operations.append(operation)
        if version != OPERATIONS_VERSION:
            self._save(operations)
        return operations

    @staticmethod
    def _validate_operation(raw: object, *, version: str = OPERATIONS_VERSION) -> dict[str, object]:
        fields = {
            "account", "action_type", "bundle_digest", "created_at", "intent",
            "external_submission_started", "failure_category", "operation_class",
            "operation_id", "phases", "state", "terminal_code", "terminal_stage",
            "updated_at",
        }
        previous_fields = fields - {
            "failure_category", "operation_class", "terminal_code", "terminal_stage",
        }
        legacy_fields = previous_fields - {"external_submission_started"}
        expected_fields = (
            legacy_fields if version == LEGACY_OPERATIONS_VERSION
            else previous_fields if version == PREVIOUS_OPERATIONS_VERSION
            else fields
        )
        if (
            not isinstance(raw, Mapping)
            or version not in {
                LEGACY_OPERATIONS_VERSION, PREVIOUS_OPERATIONS_VERSION, OPERATIONS_VERSION,
            }
            or set(raw) != expected_fields
        ):
            raise PersistenceError("PerpDEX operation is invalid")
        try:
            action = ActionType(raw["action_type"])
            if not isinstance(raw["intent"], Mapping):
                raise ContractError("Invalid intent")
            PerpDexActionIntent.from_mapping(action.value, raw["intent"])
            operation_id = raw["operation_id"]
            if (
                not isinstance(operation_id, str)
                or _ACTION_RE.fullmatch(operation_id) is None
                or uuid.UUID(operation_id[4:]).version != 4
            ):
                raise ValueError
        except (TypeError, ValueError, ContractError) as exc:
            raise PersistenceError("PerpDEX operation is invalid") from exc
        if (
            raw.get("state") not in OPERATION_STATES
            or not isinstance(raw.get("account"), str)
            or _ADDRESS_RE.fullmatch(raw["account"]) is None
            or not isinstance(raw.get("bundle_digest"), str)
            or _DIGEST_RE.fullmatch(raw["bundle_digest"]) is None
            or not isinstance(raw.get("phases"), list)
            or not raw["phases"] or len(raw["phases"]) > 8
        ):
            raise PersistenceError("PerpDEX operation is invalid")
        PerpDexOperationStore._validate_timestamp(raw.get("created_at"))
        PerpDexOperationStore._validate_timestamp(raw.get("updated_at"))
        phases: list[dict[str, object]] = []
        phase_ids: set[str] = set()
        phase_fields = {
            "cloid", "code", "nonce", "phase_id", "phase_type", "public_id",
            "state", "wire_digest",
        }
        for item in raw["phases"]:
            if (
                not isinstance(item, Mapping)
                or set(item) != phase_fields
                or item.get("state") not in PHASE_STATES
                or not isinstance(item.get("phase_id"), str)
                or _PHASE_RE.fullmatch(item["phase_id"]) is None
                or item["phase_id"] in phase_ids
                or not isinstance(item.get("phase_type"), str)
                or item["phase_type"] not in {phase.value for phase in PhaseType}
                or not isinstance(item.get("nonce"), str)
                or not item["nonce"].isdigit() or len(item["nonce"]) > 20
                or not isinstance(item.get("wire_digest"), str)
                or _DIGEST_RE.fullmatch(item["wire_digest"]) is None
                or item.get("cloid") is not None
                and (
                    not isinstance(item["cloid"], str)
                    or _CLOID_RE.fullmatch(item["cloid"]) is None
                )
                or item.get("code") is not None and not isinstance(item["code"], str)
                or isinstance(item.get("code"), str)
                and _CODE_RE.fullmatch(item["code"]) is None
                or item.get("public_id") is not None and not isinstance(item["public_id"], str)
                or isinstance(item.get("public_id"), str)
                and len(item["public_id"]) > 256
            ):
                raise PersistenceError("PerpDEX phase state is invalid")
            phase_ids.add(item["phase_id"])
            phases.append(dict(item))
        operation = dict(raw)
        if version == LEGACY_OPERATIONS_VERSION:
            operation["external_submission_started"] = _legacy_submission_started(operation)
        elif type(operation.get("external_submission_started")) is not bool:
            raise PersistenceError("PerpDEX operation is invalid")
        if version != OPERATIONS_VERSION:
            operation.update(_legacy_terminal_diagnostics(operation))
        if (
            operation.get("terminal_code") is not None
            and (
                not isinstance(operation["terminal_code"], str)
                or _CODE_RE.fullmatch(operation["terminal_code"]) is None
            )
            or operation.get("terminal_stage") is not None
            and operation.get("terminal_stage") not in TERMINAL_STAGES
            or operation.get("failure_category") is not None
            and operation.get("failure_category") not in FAILURE_CATEGORIES
            or operation.get("operation_class") is not None
            and operation.get("operation_class") not in OPERATION_CLASSES
            or operation["state"] not in TERMINAL_STATES
            and any(
                operation.get(field) is not None for field in (
                    "terminal_code", "terminal_stage", "failure_category", "operation_class",
                )
            )
        ):
            raise PersistenceError("PerpDEX terminal diagnostics are invalid")
        operation["intent"] = dict(raw["intent"]) if isinstance(raw["intent"], Mapping) else raw["intent"]
        operation["phases"] = phases
        try:
            canonical_json(operation)
        except ContractError as exc:
            raise PersistenceError("PerpDEX operation is invalid") from exc
        return operation

    def _save(self, operations: list[dict[str, object]]) -> None:
        _atomic_write(self.path, {
            "operations": operations[-MAX_OPERATIONS:],
            "operations_version": OPERATIONS_VERSION,
        })

    def begin(self, bundle: ProtectedActionBundle) -> dict[str, object]:
        bundle.validate_digest()
        with self._lock:
            operations = self._load()
            if any(item["operation_id"] == bundle.operation_id for item in operations):
                raise PersistenceError("Duplicate PerpDEX operation")
            now = self._timestamp(self._clock())
            operation = {
                "account": bundle.account,
                "action_type": bundle.intent.action_type.value,
                "bundle_digest": bundle.bundle_digest,
                "created_at": bundle.created_at,
                "external_submission_started": False,
                "failure_category": None,
                "intent": bundle.intent.to_mapping(),
                "operation_class": None,
                "operation_id": bundle.operation_id,
                "phases": [
                    {
                        "cloid": phase.cloid,
                        "code": None,
                        "nonce": phase.nonce,
                        "phase_id": phase.phase_id,
                        "phase_type": phase.phase_type.value,
                        "public_id": None,
                        "state": "PENDING",
                        "wire_digest": phase.wire_digest,
                    }
                    for phase in bundle.phases
                ],
                "state": "PREPARED",
                "terminal_code": None,
                "terminal_stage": None,
                "updated_at": now,
            }
            operations.append(operation)
            self._save(operations)
            return dict(operation)

    def mark_external_submission_started(self, operation_id: str) -> dict[str, object]:
        """Durably mark the exact moment after signing, before external send."""
        return self._update(operation_id, None, external_submission_started=True)

    def discard_pre_submit_cancelled(self, operation_id: str) -> bool:
        """Remove only a cancelled bundle that is provably still local."""
        with self._lock:
            operations = self._load()
            retained: list[dict[str, object]] = []
            discarded = False
            for operation in operations:
                if operation["operation_id"] != operation_id:
                    retained.append(operation)
                    continue
                safe_local_cancel = (
                    operation["state"] == "REJECTED"
                    and operation["external_submission_started"] is False
                    and all(
                        phase["state"] == "PENDING" and phase["public_id"] is None
                        for phase in operation["phases"]
                    )
                )
                if safe_local_cancel:
                    discarded = True
                else:
                    retained.append(operation)
            if discarded:
                self._save(retained)
            return discarded

    def prune_transient(self) -> int:
        """Keep any potentially external result; trim only old local refusals."""
        with self._lock:
            operations = self._load()
            now = self._clock()
            retained = [
                operation for operation in operations
                if not (
                    operation["external_submission_started"] is False
                    and operation["state"] in {"FAILED", "EXPIRED", "REJECTED"}
                    and now - datetime.fromisoformat(
                        str(operation["updated_at"]).removesuffix("Z") + "+00:00"
                    ).timestamp() >= TRANSIENT_OPERATION_SECONDS
                )
            ]
            if len(retained) != len(operations):
                self._save(retained)
            return len(operations) - len(retained)

    def mark_operation(
        self, operation_id: str, state: str, *, terminal_code: str | None = None,
        terminal_stage: str | None = None, failure_category: str | None = None,
        operation_class: str | None = None,
    ) -> dict[str, object]:
        if state not in OPERATION_STATES:
            raise PersistenceError("Invalid PerpDEX operation state")
        if state in TERMINAL_STATES and terminal_code is None:
            terminal_code = {
                "COMPLETED": "PERPDEX_ACTION_COMPLETED",
                "FAILED": "PERPDEX_ACTION_FAILED",
                "PARTIAL": "IOC_PARTIAL_FILL",
                "UNKNOWN": "PERPDEX_RESULT_UNKNOWN",
                "PENDING_CREDIT": "HYPERLIQUID_CREDIT_PENDING",
                "REJECTED": "ACTION_CANCELLED",
                "EXPIRED": "ACTION_EXPIRED",
            }[state]
        if state in TERMINAL_STATES and terminal_stage is None:
            terminal_stage = (
                "RECONCILIATION"
                if state in {"COMPLETED", "PARTIAL", "UNKNOWN", "PENDING_CREDIT"}
                else "TERMINAL"
            )
        if state == "REJECTED" and failure_category is None:
            failure_category = "cancelled"
        elif state == "EXPIRED" and failure_category is None:
            failure_category = "expired"
        if (
            terminal_code is not None
            and (not isinstance(terminal_code, str) or _CODE_RE.fullmatch(terminal_code) is None)
            or terminal_stage is not None and terminal_stage not in TERMINAL_STAGES
            or failure_category is not None and failure_category not in FAILURE_CATEGORIES
            or operation_class is not None and operation_class not in OPERATION_CLASSES
            or state not in TERMINAL_STATES
            and any(item is not None for item in (
                terminal_code, terminal_stage, failure_category, operation_class,
            ))
        ):
            raise PersistenceError("Invalid PerpDEX terminal diagnostics")
        return self._update(
            operation_id, None, state=state, terminal_code=terminal_code,
            terminal_stage=terminal_stage, failure_category=failure_category,
            operation_class=operation_class,
        )

    def mark_phase(
        self, operation_id: str, phase_id: str, state: str, *,
        code: str | None = None, public_id: str | None = None,
    ) -> dict[str, object]:
        if state not in PHASE_STATES:
            raise PersistenceError("Invalid PerpDEX phase state")
        return self._update(
            operation_id, phase_id, phase_state=state, code=code,
            public_id=public_id,
        )

    def _update(self, operation_id: str, phase_id: str | None, **changes) -> dict[str, object]:
        with self._lock:
            operations = self._load()
            selected: dict[str, object] | None = None
            for operation in operations:
                if operation["operation_id"] != operation_id:
                    continue
                selected = operation
                if "state" in changes:
                    previous_state = str(operation["state"])
                    next_state = str(changes["state"])
                    if (
                        next_state != previous_state
                        and next_state not in OPERATION_TRANSITIONS.get(
                            previous_state, frozenset(),
                        )
                    ):
                        raise PersistenceError("Invalid PerpDEX operation transition")
                    operation["state"] = changes["state"]
                if "external_submission_started" in changes:
                    if changes["external_submission_started"] is not True:
                        raise PersistenceError("Invalid PerpDEX submission state")
                    operation["external_submission_started"] = True
                for field in (
                    "terminal_code", "terminal_stage", "failure_category", "operation_class",
                ):
                    if field in changes:
                        operation[field] = changes[field]
                if phase_id is not None:
                    phase = next(
                        (item for item in operation["phases"] if item["phase_id"] == phase_id),
                        None,
                    )
                    if phase is None:
                        raise PersistenceError("PerpDEX phase is unavailable")
                    previous_phase_state = str(phase["state"])
                    next_phase_state = str(changes["phase_state"])
                    if (
                        next_phase_state != previous_phase_state
                        and next_phase_state not in PHASE_TRANSITIONS.get(
                            previous_phase_state, frozenset(),
                        )
                    ):
                        raise PersistenceError("Invalid PerpDEX phase transition")
                    phase["state"] = changes["phase_state"]
                    phase["code"] = changes["code"]
                    phase["public_id"] = changes["public_id"]
                operation["updated_at"] = self._timestamp(self._clock())
                break
            if selected is None:
                raise PersistenceError("PerpDEX operation is unavailable")
            self._save(operations)
            return dict(selected)

    def status(self, operation_id: str) -> dict[str, object] | None:
        with self._lock:
            for operation in self._load():
                if operation["operation_id"] == operation_id:
                    return json.loads(json.dumps(operation))
        return None

    def contain_stale(self) -> int:
        """Atomically terminalize an interrupted bundle without resuming it."""
        with self._lock:
            operations = self._load()
            now = self._clock()
            changed = 0
            for operation in operations:
                if operation["state"] in TERMINAL_STATES:
                    continue
                updated = datetime.fromisoformat(
                    str(operation["updated_at"]).removesuffix("Z") + "+00:00"
                ).timestamp()
                if now - updated < STALE_OPERATION_SECONDS:
                    continue
                if operation["state"] == "EXECUTING":
                    operation["state"] = "UNKNOWN"
                    operation["terminal_code"] = "PERPDEX_INTERRUPTED_RESULT_UNKNOWN"
                    operation["terminal_stage"] = "RECONCILIATION"
                    operation["failure_category"] = "exchange_unknown"
                    for phase in operation["phases"]:
                        if phase["state"] == "SUBMITTING":
                            phase["state"] = "UNKNOWN"
                            phase["code"] = "PERPDEX_INTERRUPTED_RESULT_UNKNOWN"
                else:
                    operation["state"] = "EXPIRED"
                    operation["terminal_code"] = "ACTION_EXPIRED"
                    operation["terminal_stage"] = "TERMINAL"
                    operation["failure_category"] = "expired"
                operation["updated_at"] = self._timestamp(now)
                changed += 1
            if changed:
                self._save(operations)
            return changed

    def latest(self, account: str | None = None) -> tuple[dict[str, object], ...]:
        with self._lock:
            self.prune_transient()
            values = self._load()
        if account is not None:
            values = [item for item in values if item["account"].lower() == account.lower()]
        return tuple(json.loads(json.dumps(item)) for item in reversed(values))
