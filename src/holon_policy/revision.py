"""Fail-closed, atomically switched local transfer-policy revisions."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .baseline import BASELINE_POLICY_DIGEST
from .loader import MAX_POLICY_BYTES, policy_digest
from .model import Policy, PolicyError

REVISION_SCHEMA_VERSION = "1"
ACTIVE_SCHEMA_VERSION = "1"
REVISION_FIELDS = frozenset({
    "revision_schema_version", "policy_revision", "policy", "policy_digest",
    "source_draft_digest",
})
ACTIVE_FIELDS = frozenset({
    "active_schema_version", "active_slot", "policy_revision", "revision_digest",
})
SLOTS = ("a", "b")
MAX_REVISION_BYTES = MAX_POLICY_BYTES + 4096


class PolicyRevisionError(ValueError):
    pass


class PolicyRevisionUnavailable(RuntimeError):
    pass


class PolicyRevisionStale(PolicyRevisionUnavailable):
    pass


def _hex_digest(value: object) -> str:
    if (
        not isinstance(value, str)
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise PolicyRevisionError("Invalid policy digest")
    return value


def canonical_revision_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")


def revision_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_revision_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class PolicySnapshot:
    policy_revision: int
    policy_digest: str
    policy: Policy
    source_draft_digest: str | None = None

    @classmethod
    def baseline(cls, policy: Policy) -> "PolicySnapshot":
        return cls(0, BASELINE_POLICY_DIGEST, policy)


@dataclass(frozen=True, slots=True)
class PolicyRevision:
    policy_revision: int
    policy: Policy
    source_draft_digest: str

    def to_dict(self) -> dict[str, Any]:
        if type(self.policy_revision) is not int or self.policy_revision <= 0:
            raise PolicyRevisionError("Invalid policy revision")
        policy_value = self.policy.to_dict()
        return {
            "revision_schema_version": REVISION_SCHEMA_VERSION,
            "policy_revision": self.policy_revision,
            "policy": policy_value,
            "policy_digest": policy_digest(policy_value),
            "source_draft_digest": _hex_digest(self.source_draft_digest),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "PolicyRevision":
        if not isinstance(value, Mapping) or set(value) != REVISION_FIELDS:
            raise PolicyRevisionError("Invalid revision fields")
        if value.get("revision_schema_version") != REVISION_SCHEMA_VERSION:
            raise PolicyRevisionError("Unsupported revision version")
        number = value.get("policy_revision")
        raw_policy = value.get("policy")
        if type(number) is not int or number <= 0 or not isinstance(raw_policy, Mapping):
            raise PolicyRevisionError("Invalid policy revision")
        try:
            policy = Policy.from_dict(raw_policy)
        except PolicyError as exc:
            raise PolicyRevisionError("Invalid revision policy") from exc
        if _hex_digest(value.get("policy_digest")) != policy_digest(policy.to_dict()):
            raise PolicyRevisionError("Revision policy digest does not match")
        result = cls(number, policy, _hex_digest(value.get("source_draft_digest")))
        if result.to_dict() != dict(value):
            raise PolicyRevisionError("Revision is not canonical")
        return result


@dataclass(frozen=True, slots=True)
class ActivePolicyPointer:
    active_slot: str
    policy_revision: int
    revision_digest: str

    def to_dict(self) -> dict[str, Any]:
        if self.active_slot not in SLOTS:
            raise PolicyRevisionError("Invalid active policy slot")
        if type(self.policy_revision) is not int or self.policy_revision <= 0:
            raise PolicyRevisionError("Invalid active policy revision")
        return {
            "active_schema_version": ACTIVE_SCHEMA_VERSION,
            "active_slot": self.active_slot,
            "policy_revision": self.policy_revision,
            "revision_digest": _hex_digest(self.revision_digest),
        }

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ActivePolicyPointer":
        if not isinstance(value, Mapping) or set(value) != ACTIVE_FIELDS:
            raise PolicyRevisionError("Invalid active policy fields")
        if value.get("active_schema_version") != ACTIVE_SCHEMA_VERSION:
            raise PolicyRevisionError("Unsupported active policy version")
        slot, number = value.get("active_slot"), value.get("policy_revision")
        if not isinstance(slot, str) or type(number) is not int:
            raise PolicyRevisionError("Invalid active policy fields")
        result = cls(slot, number, _hex_digest(value.get("revision_digest")))
        if result.to_dict() != dict(value):
            raise PolicyRevisionError("Active policy is not canonical")
        return result


class PolicyRevisionStore:
    def __init__(self, data_dir: Path, baseline_policy: Policy) -> None:
        self.data_dir = data_dir
        self.baseline_policy = baseline_policy
        self.active_path = data_dir / "authority-policy-active.json"
        self.legacy_active_path = data_dir / "transfer-policy-active.json"

    def slot_path(self, slot: str) -> Path:
        if slot not in SLOTS:
            raise PolicyRevisionError("Invalid policy slot")
        return self.data_dir / f"authority-policy-slot-{slot}.json"

    def legacy_slot_path(self, slot: str) -> Path:
        if slot not in SLOTS:
            raise PolicyRevisionError("Invalid policy slot")
        return self.data_dir / f"transfer-policy-slot-{slot}.json"

    def load(self) -> PolicySnapshot:
        active_path = (
            self.active_path if self.active_path.exists()
            else self.legacy_active_path if self.legacy_active_path.exists()
            else None
        )
        if active_path is None:
            return PolicySnapshot.baseline(self.baseline_policy)
        try:
            pointer = ActivePolicyPointer.from_dict(self._read(active_path))
            slot_path = (
                self.slot_path(pointer.active_slot)
                if active_path == self.active_path
                else self.legacy_slot_path(pointer.active_slot)
            )
            raw_revision = self._read(slot_path)
            revision = PolicyRevision.from_dict(raw_revision)
            if (
                revision.policy_revision != pointer.policy_revision
                or revision_digest(raw_revision) != pointer.revision_digest
            ):
                raise PolicyRevisionError("Active revision does not match")
            return PolicySnapshot(
                revision.policy_revision, policy_digest(revision.policy.to_dict()),
                revision.policy, revision.source_draft_digest,
            )
        except (OSError, UnicodeError, json.JSONDecodeError, PolicyRevisionError) as exc:
            raise PolicyRevisionUnavailable("Active transfer policy is unavailable") from exc

    def migrate_to_v4(self) -> tuple[PolicySnapshot, bool]:
        """Atomically remove legacy Lending authority from an active revision.

        The migration never preserves an enabled transfer switch. Re-enabling ordinary
        transfers remains a separate reviewed policy-control action.
        """
        current = self.load()
        if current.policy.schema_version == "4":
            return current, False
        if current.policy_revision == 0:
            if self.baseline_policy.schema_version != "4":
                raise PolicyRevisionUnavailable("Legacy baseline cannot be migrated")
            return PolicySnapshot.baseline(self.baseline_policy), False
        source = current.source_draft_digest
        if source is None:
            raise PolicyRevisionUnavailable("Legacy policy source is unavailable")
        return self.apply(
            current.policy.transfer_only_v4(enabled=False), source,
            current.policy_revision, current.policy_digest,
        )

    def apply(
        self, policy: Policy, source_draft_digest: str,
        expected_revision: int, expected_policy_digest: str, *, repair: bool = False,
        require_disabled: bool = True,
    ) -> tuple[PolicySnapshot, bool]:
        try:
            current = self.load()
        except PolicyRevisionUnavailable:
            if not repair:
                raise
            current, recovery_slot = self._recoverable()
        else:
            recovery_slot = None
        if (
            current.policy_revision != expected_revision
            or current.policy_digest != _hex_digest(expected_policy_digest)
        ):
            raise PolicyRevisionStale("Active policy changed; review it again")
        if require_disabled and (
            policy.authority_enabled or policy.lending_authority_enabled
        ):
            raise PolicyRevisionUnavailable("Policy authority must remain disabled")
        if (
            not repair
            and current.policy_revision > 0
            and policy_digest(policy.to_dict()) == current.policy_digest
        ):
            return current, False
        active_slot = recovery_slot if repair else self._active_slot()
        target_slot = "b" if active_slot == "a" else "a"
        revision = PolicyRevision(
            current.policy_revision + 1, policy, _hex_digest(source_draft_digest),
        )
        revision_value = revision.to_dict()
        pointer = ActivePolicyPointer(
            target_slot, revision.policy_revision, revision_digest(revision_value),
        )
        try:
            self._write(self.slot_path(target_slot), revision_value)
            if self._read(self.slot_path(target_slot)) != revision_value:
                raise PolicyRevisionError("Written revision did not verify")
            self._write(self.active_path, pointer.to_dict())
            snapshot = self.load()
        except (OSError, UnicodeError, json.JSONDecodeError, PolicyRevisionError) as exc:
            raise PolicyRevisionUnavailable("Policy revision could not be applied") from exc
        return snapshot, True

    def recoverable_snapshot(self) -> PolicySnapshot:
        return self._recoverable()[0]

    def _recoverable(self) -> tuple[PolicySnapshot, str | None]:
        candidates: list[tuple[PolicySnapshot, str]] = []
        for legacy in (False, True):
            for slot in SLOTS:
                path = self.legacy_slot_path(slot) if legacy else self.slot_path(slot)
                try:
                    revision = PolicyRevision.from_dict(self._read(path))
                except (OSError, UnicodeError, json.JSONDecodeError, PolicyRevisionError):
                    continue
                candidates.append((PolicySnapshot(
                    revision.policy_revision, policy_digest(revision.policy.to_dict()),
                    revision.policy, revision.source_draft_digest,
                ), slot))
        if not candidates:
            return PolicySnapshot.baseline(self.baseline_policy), None
        return max(candidates, key=lambda item: item[0].policy_revision)

    def _active_slot(self) -> str | None:
        active_path = (
            self.active_path if self.active_path.exists()
            else self.legacy_active_path if self.legacy_active_path.exists()
            else None
        )
        if active_path is None:
            return None
        try:
            return ActivePolicyPointer.from_dict(self._read(active_path)).active_slot
        except (OSError, UnicodeError, json.JSONDecodeError, PolicyRevisionError) as exc:
            raise PolicyRevisionUnavailable("Active transfer policy is unavailable") from exc

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        raw = path.read_bytes()
        if len(raw) > MAX_REVISION_BYTES:
            raise PolicyRevisionError("Policy revision is oversized")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise PolicyRevisionError("Policy revision must be an object")
        return value

    @staticmethod
    def _write(path: Path, value: Mapping[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb", dir=path.parent, prefix=f".{path.name}.",
                suffix=".tmp", delete=False,
            ) as stream:
                stream.write(canonical_revision_bytes(value) + b"\n")
                stream.flush()
                os.fsync(stream.fileno())
                temporary = Path(stream.name)
            os.replace(temporary, path)
            temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
