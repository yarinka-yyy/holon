"""Strict versioned default-deny policy model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

POLICY_FIELDS = frozenset(
    {"schema_version", "policy_version", "authority_enabled", "transfer_rules"}
)
RULE_FIELDS = frozenset({
    "network", "asset", "chain_id", "max_amount_atomic", "max_total_fee_wei",
    "recipients",
})
RECIPIENT_FIELDS = frozenset({"address", "max_amount_atomic"})
NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
DECIMAL_RE = re.compile(r"^[1-9][0-9]{0,77}$")
ADDRESS_RE = re.compile(r"^0x[0-9A-Fa-f]{40}$")


class PolicyError(ValueError):
    pass

@dataclass(frozen=True, slots=True)
class RecipientRule:
    address: str
    max_amount_atomic: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "RecipientRule":
        if not isinstance(value, Mapping) or set(value) != RECIPIENT_FIELDS:
            raise PolicyError("Invalid recipient rule fields")
        address = value.get("address")
        limit = value.get("max_amount_atomic")
        if not isinstance(address, str) or ADDRESS_RE.fullmatch(address) is None:
            raise PolicyError("Invalid recipient address")
        if not isinstance(limit, str) or DECIMAL_RE.fullmatch(limit) is None:
            raise PolicyError("Invalid recipient limit")
        return cls(address.lower(), limit)

    def to_dict(self) -> dict[str, Any]:
        return {"address": self.address, "max_amount_atomic": self.max_amount_atomic}

@dataclass(frozen=True, slots=True)
class TransferRule:
    network: str
    asset: str
    chain_id: int
    max_amount_atomic: str
    max_total_fee_wei: str
    recipients: tuple[RecipientRule, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "TransferRule":
        if not isinstance(value, Mapping) or set(value) != RULE_FIELDS:
            raise PolicyError("Invalid transfer rule fields")
        for field in ("network", "asset"):
            if not isinstance(value.get(field), str) or NAME_RE.fullmatch(value[field]) is None:
                raise PolicyError("Invalid transfer rule identifier")
        if type(value.get("chain_id")) is not int or value["chain_id"] <= 0:
            raise PolicyError("Invalid transfer rule chain")
        for field in ("max_amount_atomic", "max_total_fee_wei"):
            if not isinstance(value.get(field), str) or DECIMAL_RE.fullmatch(value[field]) is None:
                raise PolicyError("Invalid transfer rule limit")
        raw_recipients = value.get("recipients")
        if not isinstance(raw_recipients, list) or not 1 <= len(raw_recipients) <= 64:
            raise PolicyError("Invalid recipient rules")
        recipients = tuple(RecipientRule.from_dict(item) for item in raw_recipients)
        addresses = {item.address for item in recipients}
        if len(addresses) != len(recipients):
            raise PolicyError("Duplicate recipient rule")
        if any(int(item.max_amount_atomic) > int(value["max_amount_atomic"]) for item in recipients):
            raise PolicyError("Recipient limit exceeds route limit")
        return cls(
            value["network"], value["asset"], value["chain_id"],
            value["max_amount_atomic"], value["max_total_fee_wei"], recipients,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "network": self.network,
            "asset": self.asset,
            "chain_id": self.chain_id,
            "max_amount_atomic": self.max_amount_atomic,
            "max_total_fee_wei": self.max_total_fee_wei,
            "recipients": [item.to_dict() for item in self.recipients],
        }

@dataclass(frozen=True, slots=True)
class Policy:
    schema_version: str
    policy_version: str
    authority_enabled: bool
    transfer_rules: tuple[TransferRule, ...]

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Policy":
        if not isinstance(value, Mapping) or set(value) != POLICY_FIELDS:
            raise PolicyError("Invalid policy fields")
        if value.get("schema_version") != "2" or value.get("policy_version") != "1":
            raise PolicyError("Unsupported policy version")
        if type(value.get("authority_enabled")) is not bool:
            raise PolicyError("Invalid authority switch")
        raw_rules = value.get("transfer_rules")
        if not isinstance(raw_rules, list) or len(raw_rules) > 64:
            raise PolicyError("Invalid transfer rules")
        rules = tuple(TransferRule.from_dict(item) for item in raw_rules)
        identities = {(rule.network, rule.asset) for rule in rules}
        if len(identities) != len(rules):
            raise PolicyError("Duplicate transfer rule")
        return cls("2", "1", value["authority_enabled"], rules)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "policy_version": self.policy_version,
            "authority_enabled": self.authority_enabled,
            "transfer_rules": [rule.to_dict() for rule in self.transfer_rules],
        }
