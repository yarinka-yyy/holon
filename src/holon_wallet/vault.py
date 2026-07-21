"""Versioned Argon2id/AES-GCM multi-profile Wallet vault."""

from __future__ import annotations

import base64
import hmac
import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.argon2 import Argon2id
from eth_utils import is_checksum_address

from .model import ProfileSummary
from .storage import StorageError, WalletPaths, atomic_write_json, read_json
from .wallet_crypto import (
    DERIVATION_PATH,
    MNEMONIC_PROFILE,
    RAW_KEY_PROFILE,
    InvalidSecretError,
    SecretMaterial,
    rederive,
)

SCHEMA_VERSION = 1
PAYLOAD_VERSION = 1
MIN_PASSWORD_LENGTH = 4
ARGON_ITERATIONS = 3
ARGON_MEMORY_KIB = 64 * 1024
ARGON_LANES = 4


class VaultUnavailableError(RuntimeError):
    """The stored envelope cannot be used without unsafe assumptions."""


class AuthenticationFailedError(RuntimeError):
    """Password or authenticated vault content could not be verified."""


class VaultValidationError(ValueError):
    """A requested profile operation is locally invalid."""


@dataclass(frozen=True, slots=True, repr=False)
class ProfileRecord:
    summary: ProfileSummary
    secret: SecretMaterial


@dataclass(frozen=True, slots=True, repr=False)
class PreparedVault:
    document: dict[str, Any]
    profiles: tuple[ProfileSummary, ...]


class VaultRepository:
    def __init__(self, paths: WalletPaths | None = None) -> None:
        self.paths = paths or WalletPaths.default()

    @property
    def exists(self) -> bool:
        return self.paths.vault.exists()

    def new_record(self, secret: SecretMaterial, label: str) -> ProfileRecord:
        summary = ProfileSummary(
            profile_id=str(uuid.uuid4()),
            label=label,
            address=secret.address,
            profile_type=secret.profile_type,
            derivation_path=secret.derivation_path,
            created_at=datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        )
        return ProfileRecord(summary, secret)

    def load_public(self) -> tuple[ProfileSummary, ...]:
        document = self._read_document()
        return self._parse_envelope(document)[0]

    def prepare_new(self, password: str, record: ProfileRecord) -> PreparedVault:
        if self.exists:
            raise VaultValidationError("Wallet already exists")
        document = self._encrypt(password, (record,))
        return PreparedVault(document, (record.summary,))

    def commit_new(self, prepared: PreparedVault) -> None:
        if self.exists:
            raise VaultValidationError("Wallet already exists")
        atomic_write_json(self.paths.vault, prepared.document)

    def create_new(self, password: str, record: ProfileRecord) -> tuple[ProfileSummary, ...]:
        prepared = self.prepare_new(password, record)
        self.commit_new(prepared)
        return prepared.profiles

    def authenticate(self, password: str) -> tuple[ProfileSummary, ...]:
        document = self._read_document()
        summaries, header, ciphertext = self._parse_envelope(document)
        self._decrypt_records(password, header, ciphertext, summaries)
        return summaries

    def _authenticate_profile(self, password: str, profile_id: str) -> ProfileRecord:
        """Return one authenticated record only to an in-process Wallet operation."""
        document = self._read_document()
        summaries, header, ciphertext = self._parse_envelope(document)
        records = self._decrypt_records(password, header, ciphertext, summaries)
        for record in records:
            if record.summary.profile_id == profile_id:
                return record
        raise AuthenticationFailedError("Authentication failed")

    def append(
        self, password: str, record: ProfileRecord,
    ) -> tuple[ProfileSummary, ...]:
        document = self._read_document()
        summaries, header, ciphertext = self._parse_envelope(document)
        records = self._decrypt_records(password, header, ciphertext, summaries)
        if any(
            existing.summary.address.lower() == record.summary.address.lower()
            for existing in records
        ):
            raise VaultValidationError("This Account already exists")
        updated = (*records, record)
        atomic_write_json(self.paths.vault, self._encrypt(password, updated))
        return tuple(item.summary for item in updated)

    def _read_document(self) -> dict[str, Any]:
        if not self.exists:
            raise VaultUnavailableError("Wallet vault is unavailable")
        try:
            document = read_json(self.paths.vault)
        except StorageError as error:
            raise VaultUnavailableError("Wallet vault is unavailable") from error
        if not isinstance(document, dict):
            raise VaultUnavailableError("Wallet vault is unavailable")
        return document

    def _encrypt(
        self, password: str, records: tuple[ProfileRecord, ...],
    ) -> dict[str, Any]:
        _validate_password(password)
        salt = os.urandom(16)
        nonce = os.urandom(12)
        header: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "kdf": {
                "name": "argon2id",
                "iterations": ARGON_ITERATIONS,
                "memory_kib": ARGON_MEMORY_KIB,
                "lanes": ARGON_LANES,
                "salt": _encode(salt),
            },
            "cipher": {"name": "aes-256-gcm", "nonce": _encode(nonce)},
            "public": {
                "profiles": [_summary_dict(record.summary) for record in records],
            },
        }
        payload = {
            "payload_version": PAYLOAD_VERSION,
            "profiles": [
                {
                    "profile_id": record.summary.profile_id,
                    "profile_type": record.secret.profile_type,
                    "secret": record.secret.value,
                }
                for record in records
            ],
        }
        key = _derive_key(password, salt)
        try:
            plaintext = _canonical(payload)
            ciphertext = AESGCM(key).encrypt(nonce, plaintext, _canonical(header))
        finally:
            del key
        return {**header, "ciphertext": _encode(ciphertext)}

    def _parse_envelope(
        self, document: dict[str, Any],
    ) -> tuple[tuple[ProfileSummary, ...], dict[str, Any], bytes]:
        if set(document) != {"schema_version", "kdf", "cipher", "public", "ciphertext"}:
            raise VaultUnavailableError("Wallet vault is unavailable")
        if document.get("schema_version") != SCHEMA_VERSION:
            raise VaultUnavailableError("Wallet schema is unsupported")
        kdf = document.get("kdf")
        cipher = document.get("cipher")
        public = document.get("public")
        if not _valid_kdf(kdf) or not _valid_cipher(cipher):
            raise VaultUnavailableError("Wallet schema is unsupported")
        if not isinstance(public, dict) or set(public) != {"profiles"}:
            raise VaultUnavailableError("Wallet vault is unavailable")
        raw_profiles = public["profiles"]
        if not isinstance(raw_profiles, list) or not raw_profiles:
            raise VaultUnavailableError("Wallet vault is unavailable")
        try:
            profiles = tuple(_summary_from_dict(item) for item in raw_profiles)
            ciphertext = _decode(document["ciphertext"])
            _decode(kdf["salt"], expected=16)
            _decode(cipher["nonce"], expected=12)
        except (TypeError, ValueError, KeyError):
            raise VaultUnavailableError("Wallet vault is unavailable") from None
        if len({profile.profile_id for profile in profiles}) != len(profiles):
            raise VaultUnavailableError("Wallet vault is unavailable")
        header = {key: document[key] for key in ("schema_version", "kdf", "cipher", "public")}
        return profiles, header, ciphertext

    def _decrypt_records(
        self,
        password: str,
        header: dict[str, Any],
        ciphertext: bytes,
        summaries: tuple[ProfileSummary, ...],
    ) -> tuple[ProfileRecord, ...]:
        _validate_password(password)
        salt = _decode(header["kdf"]["salt"], expected=16)
        nonce = _decode(header["cipher"]["nonce"], expected=12)
        key = _derive_key(password, salt)
        try:
            plaintext = AESGCM(key).decrypt(nonce, ciphertext, _canonical(header))
        except (InvalidTag, ValueError):
            raise AuthenticationFailedError("Authentication failed") from None
        finally:
            del key
        try:
            payload = json.loads(plaintext.decode("utf-8"))
            records = _records_from_payload(payload, summaries)
            for record in records:
                if not hmac.compare_digest(
                    rederive(record.secret).lower(), record.summary.address.lower(),
                ):
                    raise ValueError
            return records
        except (InvalidSecretError, KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
            raise AuthenticationFailedError("Authentication failed") from None


def _records_from_payload(
    payload: object, summaries: tuple[ProfileSummary, ...],
) -> tuple[ProfileRecord, ...]:
    if not isinstance(payload, dict) or set(payload) != {"payload_version", "profiles"}:
        raise ValueError
    if payload["payload_version"] != PAYLOAD_VERSION or not isinstance(payload["profiles"], list):
        raise ValueError
    if len(payload["profiles"]) != len(summaries):
        raise ValueError
    records: list[ProfileRecord] = []
    for raw, summary in zip(payload["profiles"], summaries, strict=True):
        if not isinstance(raw, dict) or set(raw) != {"profile_id", "profile_type", "secret"}:
            raise ValueError
        if raw["profile_id"] != summary.profile_id or raw["profile_type"] != summary.profile_type:
            raise ValueError
        if not isinstance(raw["secret"], str):
            raise ValueError
        material = SecretMaterial(
            raw["profile_type"], raw["secret"], summary.address, summary.derivation_path,
        )
        records.append(ProfileRecord(summary, material))
    return tuple(records)


def _summary_dict(summary: ProfileSummary) -> dict[str, Any]:
    return {
        "profile_id": summary.profile_id,
        "label": summary.label,
        "type": summary.profile_type,
        "address": summary.address,
        "derivation_path": summary.derivation_path,
        "created_at": summary.created_at,
    }


def _summary_from_dict(value: object) -> ProfileSummary:
    fields = {"profile_id", "label", "type", "address", "derivation_path", "created_at"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError
    profile_type = value["type"]
    path = value["derivation_path"]
    if profile_type not in {MNEMONIC_PROFILE, RAW_KEY_PROFILE}:
        raise ValueError
    if path != (DERIVATION_PATH if profile_type == MNEMONIC_PROFILE else None):
        raise ValueError
    for field in ("profile_id", "label", "address", "created_at"):
        if not isinstance(value[field], str) or not value[field]:
            raise ValueError
    uuid.UUID(value["profile_id"])
    address = value["address"]
    if len(address) != 42 or not address.startswith("0x") or not is_checksum_address(address):
        raise ValueError
    return ProfileSummary(
        value["profile_id"], value["label"], address, profile_type, path, value["created_at"],
    )


def _valid_kdf(value: object) -> bool:
    return isinstance(value, dict) and value.keys() == {
        "name", "iterations", "memory_kib", "lanes", "salt",
    } and value.get("name") == "argon2id" and value.get("iterations") == ARGON_ITERATIONS \
        and value.get("memory_kib") == ARGON_MEMORY_KIB and value.get("lanes") == ARGON_LANES


def _valid_cipher(value: object) -> bool:
    return isinstance(value, dict) and value.keys() == {"name", "nonce"} \
        and value.get("name") == "aes-256-gcm"


def _validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < MIN_PASSWORD_LENGTH:
        raise VaultValidationError("Password must contain at least 4 characters")


def _derive_key(password: str, salt: bytes) -> bytes:
    return Argon2id(
        salt=salt,
        length=32,
        iterations=ARGON_ITERATIONS,
        lanes=ARGON_LANES,
        memory_cost=ARGON_MEMORY_KIB,
    ).derive(password.encode("utf-8"))


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")


def _encode(value: bytes) -> str:
    return base64.b64encode(value).decode("ascii")


def _decode(value: object, expected: int | None = None) -> bytes:
    if not isinstance(value, str):
        raise TypeError
    decoded = base64.b64decode(value, validate=True)
    if expected is not None and len(decoded) != expected:
        raise ValueError
    return decoded
