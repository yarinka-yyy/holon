"""Exact, single-use authority for local recovery-material display."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import TypeVar

from .model import ProfileSummary
from .wallet_crypto import DERIVATION_PATH, MNEMONIC_PROFILE, RAW_KEY_PROFILE

RECOVERY_ACTION_LIFETIME = timedelta(minutes=5)
T = TypeVar("T")


class RecoveryMaterialKind(str, Enum):
    SEED_PHRASE = "seed_phrase"
    PRIVATE_KEY = "private_key"


class RecoveryFlowState(str, Enum):
    LOCKED = "LOCKED"
    PREPARED = "PREPARED"
    AUTHORIZED = "AUTHORIZED"


class RecoveryActionError(RuntimeError):
    """A recovery action is missing, stale, replayed, or mismatched."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _new_action_id() -> str:
    return f"recovery-{uuid.uuid4()}"


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z",
    )


@dataclass(frozen=True, slots=True)
class PreparedRecoveryAction:
    action_id: str
    profile_id: str
    account_label: str
    address: str
    profile_type: str
    material_kind: RecoveryMaterialKind
    derivation_path: str | None
    permission: str
    created_at: datetime
    expires_at: datetime

    def material_fields(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "profile_id": self.profile_id,
            "account_label": self.account_label,
            "address": self.address,
            "profile_type": self.profile_type,
            "material_kind": self.material_kind.value,
            "derivation_path": self.derivation_path,
            "permission": self.permission,
            "created_at": _timestamp(self.created_at),
            "expires_at": _timestamp(self.expires_at),
        }

    @property
    def digest(self) -> str:
        encoded = json.dumps(
            self.material_fields(),
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()


def recovery_action_to_map(action: PreparedRecoveryAction) -> dict[str, object]:
    return {
        "actionId": action.action_id,
        "accountLabel": action.account_label,
        "address": action.address,
        "profileType": action.profile_type,
        "materialKind": action.material_kind.value,
        "materialLabel": (
            "Seed Phrase"
            if action.material_kind is RecoveryMaterialKind.SEED_PHRASE
            else "Private Key"
        ),
        "derivationPath": action.derivation_path or "",
        "permission": action.permission,
        "expiresAt": _timestamp(action.expires_at),
        "digest": action.digest,
    }


class RecoveryFlowCoordinator:
    """Own one transient recovery action and consume it exactly once."""

    def __init__(
        self,
        clock: Callable[[], datetime] = _utc_now,
        action_id_factory: Callable[[], str] = _new_action_id,
    ) -> None:
        self._clock = clock
        self._action_id_factory = action_id_factory
        self._state = RecoveryFlowState.LOCKED
        self._current: PreparedRecoveryAction | None = None
        self._terminal_ids: set[str] = set()

    @property
    def state(self) -> RecoveryFlowState:
        return self._state

    @property
    def current(self) -> PreparedRecoveryAction | None:
        return self._current

    def prepare(
        self,
        profile: ProfileSummary,
        material_kind: RecoveryMaterialKind,
    ) -> PreparedRecoveryAction:
        if self._current is not None or self._state is not RecoveryFlowState.LOCKED:
            raise RecoveryActionError("A protected Wallet action is already active")
        _validate_material_kind(profile, material_kind)
        action_id = self._action_id_factory()
        if action_id in self._terminal_ids:
            raise RecoveryActionError("A terminal recovery action cannot be reused")
        created_at = self._clock().astimezone(UTC)
        action = PreparedRecoveryAction(
            action_id=action_id,
            profile_id=profile.profile_id,
            account_label=profile.label,
            address=profile.address,
            profile_type=profile.profile_type,
            material_kind=material_kind,
            derivation_path=(
                DERIVATION_PATH
                if material_kind is RecoveryMaterialKind.PRIVATE_KEY
                and profile.profile_type == MNEMONIC_PROFILE
                else None
            ),
            permission="reveal_and_copy_once",
            created_at=created_at,
            expires_at=created_at + RECOVERY_ACTION_LIFETIME,
        )
        self._current = action
        self._state = RecoveryFlowState.PREPARED
        return action

    def preflight(
        self,
        action_id: str,
        digest: str,
        profile: ProfileSummary,
    ) -> PreparedRecoveryAction:
        current = self._current
        if current is None:
            if action_id in self._terminal_ids:
                raise RecoveryActionError("A terminal recovery action cannot be reused")
            raise RecoveryActionError("Recovery action is unavailable")
        if self._state is not RecoveryFlowState.PREPARED:
            self._terminalize()
            raise RecoveryActionError("Recovery action is unavailable")
        if (
            current.action_id != action_id
            or current.digest != digest
            or current.profile_id != profile.profile_id
            or current.account_label != profile.label
            or current.address != profile.address
            or current.profile_type != profile.profile_type
        ):
            self._terminalize()
            raise RecoveryActionError("Recovery action changed")
        if self._clock().astimezone(UTC) >= current.expires_at:
            self._terminalize()
            raise RecoveryActionError("Recovery action expired")
        return current

    def authorize_and_consume(
        self,
        action_id: str,
        digest: str,
        profile: ProfileSummary,
        consumer: Callable[[PreparedRecoveryAction], T],
    ) -> T:
        current = self.preflight(action_id, digest, profile)
        self._state = RecoveryFlowState.AUTHORIZED
        try:
            result = consumer(current)
        except Exception:
            self._terminalize()
            raise
        self._terminalize()
        return result

    def authentication_failed(self) -> None:
        self._terminalize()

    def cancel(self) -> None:
        self._terminalize()

    def profile_changed(self, profile_id: str) -> bool:
        if self._current is None or self._current.profile_id == profile_id:
            return False
        self._terminalize()
        return True

    def close(self) -> None:
        self._terminalize()

    def _terminalize(self) -> None:
        if self._current is not None:
            self._terminal_ids.add(self._current.action_id)
        self._current = None
        self._state = RecoveryFlowState.LOCKED


def _validate_material_kind(
    profile: ProfileSummary,
    material_kind: RecoveryMaterialKind,
) -> None:
    if profile.profile_type == MNEMONIC_PROFILE:
        if material_kind not in {
            RecoveryMaterialKind.SEED_PHRASE,
            RecoveryMaterialKind.PRIVATE_KEY,
        }:
            raise RecoveryActionError("Recovery material type is unsupported")
        return
    if (
        profile.profile_type == RAW_KEY_PROFILE
        and material_kind is RecoveryMaterialKind.PRIVATE_KEY
    ):
        return
    raise RecoveryActionError("Recovery material type is unavailable for this Account")
