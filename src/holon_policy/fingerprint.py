"""Policy-side semantic fingerprint; this is not a Wallet signing digest."""

from __future__ import annotations

import hashlib
import json

from holon_contracts import ContractEnvelope

MATERIAL_FIELDS = (
    "policy_version",
    "action_type",
    "network",
    "asset",
    "amount_atomic",
    "recipient",
    "max_total_fee_wei",
)


def action_fingerprint(envelope: ContractEnvelope) -> str:
    material = {"schema_version": envelope.schema_version}
    material.update({field: envelope.payload[field] for field in MATERIAL_FIELDS})
    canonical = json.dumps(material, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()
