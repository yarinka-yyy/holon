"""Canonical policy integrity loading without external dependencies."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from holon_contracts import SecurityCode

from .model import Policy, PolicyError

MAX_POLICY_BYTES = 64 * 1024


class PolicyLoadError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__("Policy is unavailable")
        self.code = code


def canonical_policy_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(
        dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True
    ).encode("utf-8")


def policy_digest(value: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_policy_bytes(value)).hexdigest()


def load_policy(path: Path, expected_digest: str) -> Policy:
    try:
        raw = path.read_bytes()
        if len(raw) > MAX_POLICY_BYTES:
            raise PolicyError("Policy is oversized")
        value = json.loads(raw.decode("utf-8"))
        if not isinstance(value, dict):
            raise PolicyError("Policy must be an object")
        policy = Policy.from_dict(value)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, PolicyError) as exc:
        raise PolicyLoadError(SecurityCode.POLICY_STATE_INVALID.value) from exc
    if policy_digest(policy.to_dict()) != expected_digest:
        raise PolicyLoadError(SecurityCode.POLICY_INTEGRITY_FAILED.value)
    return policy
