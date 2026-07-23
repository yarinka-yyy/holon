"""Canonical semantic duplicate fingerprint for protected requests."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

SEMANTIC_FIELDS = ("action_type", "network", "recipient", "asset", "amount_atomic")


def semantic_fingerprint(
    payload: Mapping[str, Any], *, contract: str | None = None,
    method: str | None = None, selector: str | None = None,
) -> str:
    if method is not None and selector is not None:
        raise ValueError("Only one semantic method identifier is allowed")
    material = {name: payload[name] for name in SEMANTIC_FIELDS}
    material["recipient"] = material["recipient"].lower()
    material["contract"] = None if contract is None else contract.lower()
    operation = method if method is not None else selector
    material["method"] = None if operation is None else operation.lower()
    canonical = json.dumps(material, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def intent_fingerprint(
    *, policy_version: str, network: str, asset: str, amount_atomic: str,
    recipient: str, max_total_fee_wei: str,
) -> str:
    material = {
        "schema_version": "1",
        "policy_version": policy_version,
        "network": network,
        "asset": asset,
        "amount_atomic": amount_atomic,
        "recipient": recipient.lower(),
        "max_total_fee_wei": max_total_fee_wei,
    }
    canonical = json.dumps(material, separators=(",", ":"), sort_keys=True).encode()
    return hashlib.sha256(canonical).hexdigest()
