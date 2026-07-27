"""Shared strict public state for one protected multi-phase Lending operation."""

from __future__ import annotations

import json
import os
import tempfile
import re
import uuid
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

SCHEMA_VERSION = "2"
MAX_BYTES = 32 * 1024
MAX_TERMINAL = 64
PHASES = {
    "approve_review", "approve_receipt", "prepare_supply", "supply_review",
    "supply_receipt", "resume_or_revoke", "completed", "failed", "cancelled",
}
TERMINAL_PHASES = {"completed", "failed", "cancelled"}
LEGACY_FIELDS = {
    "schema_version", "operation_id", "requested_action", "amount_mode", "amount",
    "resolved_amount_atomic", "owner_pid", "policy_version", "policy_revision",
    "policy_digest", "action_profile_digest", "safety_digest", "phase",
    "phase_action_id", "phase_fingerprint", "created_at",
    "account_profile_id", "account_address",
    "transaction_hash", "receipt_state", "updated_at",
}
FIELDS = LEGACY_FIELDS | {"protocol_profile_id", "protocol_id"}
RECEIPT_STATES = {"none", "pending", "unknown", "confirmed", "failed"}
ACTION_RE = re.compile(r"^act-[0-9a-f-]{36}$")
HEX_RE = re.compile(r"^[0-9a-f]{64}$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")


def _action(value: str) -> bool:
    if ACTION_RE.fullmatch(value) is None:
        return False
    try:
        parsed = uuid.UUID(value[4:])
    except ValueError:
        return False
    return parsed.version == 4 and str(parsed) == value[4:]


class LendingOperationStateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class LendingOperation:
    operation_id: str
    requested_action: str
    amount_mode: str
    amount: str | None
    resolved_amount_atomic: int
    owner_pid: int
    policy_version: str
    policy_revision: int
    policy_digest: str
    action_profile_digest: str
    safety_digest: str
    phase: str
    phase_action_id: str
    phase_fingerprint: str
    created_at: str
    account_profile_id: str
    account_address: str
    transaction_hash: str | None = None
    receipt_state: str = "none"
    updated_at: str = ""
    protocol_profile_id: str = "aave-v3-base-usdc"
    protocol_id: str = "aave-v3"

    def with_phase(
        self, phase: str, *, phase_action_id: str | None = None,
        phase_fingerprint: str | None = None,
    ) -> "LendingOperation":
        if phase not in PHASES:
            raise LendingOperationStateError("Invalid lending operation phase")
        return replace(
            self, phase=phase,
            phase_action_id=phase_action_id or self.phase_action_id,
            phase_fingerprint=phase_fingerprint or self.phase_fingerprint,
        )

    def resume(self, owner_pid: int) -> "LendingOperation":
        if (
            self.phase != "resume_or_revoke" or owner_pid <= 0
            or self.receipt_state != "confirmed"
        ):
            raise LendingOperationStateError("Lending operation cannot resume")
        return replace(self, owner_pid=owner_pid, phase="prepare_supply")

    def with_receipt(
        self, transaction_hash: str, receipt_state: str, updated_at: str,
    ) -> "LendingOperation":
        if (
            receipt_state not in RECEIPT_STATES - {"none"}
            or not transaction_hash.startswith("0x") or len(transaction_hash) != 66
            or any(character not in "0123456789abcdef" for character in transaction_hash[2:])
            or not updated_at
        ):
            raise LendingOperationStateError("Invalid lending receipt state")
        return replace(
            self, transaction_hash=transaction_hash,
            receipt_state=receipt_state, updated_at=updated_at,
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema_version": SCHEMA_VERSION,
            "operation_id": self.operation_id,
            "requested_action": self.requested_action,
            "amount_mode": self.amount_mode,
            "amount": self.amount,
            "resolved_amount_atomic": str(self.resolved_amount_atomic),
            "owner_pid": self.owner_pid,
            "policy_version": self.policy_version,
            "policy_revision": self.policy_revision,
            "policy_digest": self.policy_digest,
            "action_profile_digest": self.action_profile_digest,
            "safety_digest": self.safety_digest,
            "phase": self.phase,
            "phase_action_id": self.phase_action_id,
            "phase_fingerprint": self.phase_fingerprint,
            "created_at": self.created_at,
            "account_profile_id": self.account_profile_id,
            "account_address": self.account_address,
            "transaction_hash": self.transaction_hash,
            "receipt_state": self.receipt_state,
            "updated_at": self.updated_at,
            "protocol_profile_id": self.protocol_profile_id,
            "protocol_id": self.protocol_id,
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LendingOperation":
        if not isinstance(value, Mapping) or frozenset(value) not in {frozenset(FIELDS), frozenset(LEGACY_FIELDS)}:
            raise LendingOperationStateError("Invalid lending operation fields")
        version = value.get("schema_version")
        if version not in {"1", SCHEMA_VERSION} or (version == "1") != (set(value) == LEGACY_FIELDS):
            raise LendingOperationStateError("Unsupported lending operation version")
        amount = value.get("amount")
        transaction_hash = value.get("transaction_hash")
        strings = (
            "operation_id", "requested_action", "amount_mode", "policy_version",
            "policy_digest", "action_profile_digest", "safety_digest", "phase",
            "phase_action_id", "phase_fingerprint", "created_at",
            "account_profile_id", "account_address",
        )
        if any(not isinstance(value.get(name), str) or not value[name] for name in strings):
            raise LendingOperationStateError("Invalid lending operation value")
        if amount is not None and not isinstance(amount, str):
            raise LendingOperationStateError("Invalid lending operation amount")
        if transaction_hash is not None and (
            not isinstance(transaction_hash, str)
            or not transaction_hash.startswith("0x") or len(transaction_hash) != 66
            or any(character not in "0123456789abcdef" for character in transaction_hash[2:])
        ):
            raise LendingOperationStateError("Invalid lending transaction hash")
        if (
            value["requested_action"] != "supply"
            or value["amount_mode"] not in {"exact", "all"}
            or value["phase"] not in PHASES
            or type(value.get("owner_pid")) is not int or value["owner_pid"] <= 0
            or type(value.get("policy_revision")) is not int or value["policy_revision"] < 0
            or value.get("receipt_state") not in RECEIPT_STATES
            or not isinstance(value.get("updated_at"), str)
            or (value["receipt_state"] == "none") != (transaction_hash is None)
            or not _action(str(value["operation_id"]))
            or not _action(str(value["phase_action_id"]))
            or any(HEX_RE.fullmatch(str(value[field])) is None for field in (
                "policy_digest", "action_profile_digest", "safety_digest",
                "phase_fingerprint",
            ))
            or ADDRESS_RE.fullmatch(str(value["account_address"])) is None
            or value["policy_version"] not in {"2", "3"}
        ):
            raise LendingOperationStateError("Invalid lending operation identity")
        protocol_profile_id = str(value.get("protocol_profile_id", "aave-v3-base-usdc"))
        protocol_id = str(value.get("protocol_id", "aave-v3"))
        if {
            "aave-v3-base-usdc": "aave-v3",
            "compound-v3-base-usdc": "compound-v3",
            "morpho-v1-gauntlet-usdc-prime": "morpho-v1",
        }.get(protocol_profile_id) != protocol_id:
            raise LendingOperationStateError("Invalid lending operation protocol")
        try:
            resolved = int(value["resolved_amount_atomic"])
        except (TypeError, ValueError) as exc:
            raise LendingOperationStateError("Invalid lending operation amount") from exc
        if resolved <= 0:
            raise LendingOperationStateError("Invalid lending operation amount")
        result = cls(
            str(value["operation_id"]), str(value["requested_action"]),
            str(value["amount_mode"]), amount, resolved, int(value["owner_pid"]),
            str(value["policy_version"]), int(value["policy_revision"]),
            str(value["policy_digest"]), str(value["action_profile_digest"]),
            str(value["safety_digest"]), str(value["phase"]),
            str(value["phase_action_id"]), str(value["phase_fingerprint"]),
            str(value["created_at"]),
            str(value["account_profile_id"]), str(value["account_address"]),
            transaction_hash, str(value["receipt_state"]), str(value["updated_at"]),
            protocol_profile_id, protocol_id,
        )
        canonical = result.to_dict()
        if version == "1":
            canonical.pop("protocol_profile_id")
            canonical.pop("protocol_id")
            canonical["schema_version"] = "1"
        if canonical != dict(value):
            raise LendingOperationStateError("Lending operation is not canonical")
        return result


@dataclass(frozen=True, slots=True)
class LendingOperationSnapshot:
    current: LendingOperation | None = None
    terminal: tuple[LendingOperation, ...] = ()


class LendingOperationStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> LendingOperationSnapshot:
        if not self.path.exists():
            return LendingOperationSnapshot()
        try:
            raw = self.path.read_bytes()
            if len(raw) > MAX_BYTES:
                raise LendingOperationStateError("Lending operation state is oversized")
            value = json.loads(raw.decode("utf-8"))
            if not isinstance(value, dict) or set(value) != {"current", "terminal"}:
                raise LendingOperationStateError("Invalid lending operation state")
            current_raw, terminal_raw = value["current"], value["terminal"]
            if current_raw is not None and not isinstance(current_raw, dict):
                raise LendingOperationStateError("Invalid current lending operation")
            if not isinstance(terminal_raw, list) or len(terminal_raw) > MAX_TERMINAL:
                raise LendingOperationStateError("Invalid terminal lending operations")
            current = LendingOperation.from_dict(current_raw) if current_raw is not None else None
            terminal = tuple(LendingOperation.from_dict(item) for item in terminal_raw)
            if any(item.phase not in TERMINAL_PHASES for item in terminal):
                raise LendingOperationStateError("Non-terminal lending operation in history")
            return LendingOperationSnapshot(current, terminal)
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise LendingOperationStateError("Lending operation state is unavailable") from exc

    def save(self, snapshot: LendingOperationSnapshot) -> None:
        terminal = snapshot.terminal[-MAX_TERMINAL:]
        if (
            snapshot.current is not None and snapshot.current.phase in TERMINAL_PHASES
            or any(item.phase not in TERMINAL_PHASES for item in terminal)
        ):
            raise LendingOperationStateError("Invalid lending operation snapshot")
        value = {
            "current": snapshot.current.to_dict() if snapshot.current is not None else None,
            "terminal": [item.to_dict() for item in terminal],
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(value, stream, separators=(",", ":"), sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except Exception:
            try:
                os.unlink(temporary)
            except OSError:
                pass
            raise
