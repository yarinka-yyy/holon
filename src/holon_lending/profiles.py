"""Pinned loading for the read-only Lending L1 identities."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .model import LendingReadProfiles, ReadProfilesValidationError

MAX_READ_PROFILES_BYTES = 64 * 1024
READ_PROFILES_PATH = Path(__file__).with_name("read-profiles.json")
READ_PROFILES_DIGEST = "cf09eb1ddd7c65703c88d92d8c7b8550ec1797aaa7c1b6fd308386793cf2624e"


class ReadProfilesLoadError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Lending read profiles are unavailable")
        self.code = code


def canonical_read_profiles_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8") + b"\n"


def read_profiles_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_read_profiles_bytes(value)).hexdigest()


def load_read_profiles(path: Path = READ_PROFILES_PATH) -> LendingReadProfiles:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ReadProfilesLoadError("READ_PROFILES_MISSING") from exc
    try:
        if len(raw) > MAX_READ_PROFILES_BYTES:
            raise ReadProfilesValidationError("read profiles are oversized")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise ReadProfilesValidationError("read profiles must be an object")
        canonical = canonical_read_profiles_bytes(value)
        if raw not in (canonical, canonical[:-1] + b"\r\n"):
            raise ReadProfilesValidationError("read profiles are not canonical")
    except (UnicodeDecodeError, json.JSONDecodeError, ReadProfilesValidationError) as exc:
        code = (
            "READ_PROFILES_INCOMPATIBLE"
            if isinstance(exc, ReadProfilesValidationError) and exc.incompatible
            else "READ_PROFILES_CORRUPT"
        )
        raise ReadProfilesLoadError(code) from exc
    if value.get("schema_version") != "1" or value.get("profile_version") != "1":
        raise ReadProfilesLoadError("READ_PROFILES_INCOMPATIBLE")
    if hashlib.sha256(canonical).hexdigest() != READ_PROFILES_DIGEST:
        raise ReadProfilesLoadError("READ_PROFILES_INTEGRITY_FAILED")
    try:
        return LendingReadProfiles.from_dict(value)
    except ReadProfilesValidationError as exc:
        raise ReadProfilesLoadError("READ_PROFILES_CORRUPT") from exc


@dataclass(frozen=True)
class ReadProfilesState:
    status: str
    profiles: LendingReadProfiles | None
    error_code: str | None

    @classmethod
    def load(cls, path: Path = READ_PROFILES_PATH) -> "ReadProfilesState":
        try:
            return cls("READY", load_read_profiles(path), None)
        except ReadProfilesLoadError as exc:
            return cls("UNAVAILABLE", None, exc.code)
