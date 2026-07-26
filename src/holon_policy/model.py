"""Strict versioned default-deny policy model."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

POLICY_V2_FIELDS = frozenset(
    {"schema_version", "policy_version", "authority_enabled", "transfer_rules"}
)
POLICY_V3_FIELDS = frozenset({
    "schema_version", "policy_version", "transfer_authority_enabled",
    "lending_authority_enabled", "transfer_rules", "lending_rules",
})
RULE_FIELDS = frozenset({
    "network", "asset", "chain_id", "max_amount_atomic", "max_total_fee_wei",
    "recipients",
})
RECIPIENT_FIELDS = frozenset({"address", "max_amount_atomic"})
LENDING_RULE_FIELDS = frozenset({
    "module_id", "module_version", "protocol_profile_id",
    "protocol_profile_version", "network", "asset", "chain_id",
    "allowed_actions", "max_amount_atomic", "max_total_fee_wei",
    "action_profile_digest",
})
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
class LendingRule:
    module_id: str
    module_version: str
    protocol_profile_id: str
    protocol_profile_version: str
    network: str
    asset: str
    chain_id: int
    allowed_actions: tuple[str, ...]
    max_amount_atomic: str
    max_total_fee_wei: str
    action_profile_digest: str

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "LendingRule":
        if not isinstance(value, Mapping) or set(value) != LENDING_RULE_FIELDS:
            raise PolicyError("Invalid lending rule fields")
        identifiers = (
            "module_id", "module_version", "protocol_profile_id",
            "protocol_profile_version", "network", "asset",
        )
        named_identifiers = ("module_id", "protocol_profile_id", "network", "asset")
        if any(
            not isinstance(value.get(field), str)
            or NAME_RE.fullmatch(value[field]) is None
            for field in named_identifiers
        ) or any(
            not isinstance(value.get(field), str)
            for field in ("module_version", "protocol_profile_version")
        ):
            raise PolicyError("Invalid lending rule identity")
        if type(value.get("chain_id")) is not int or value["chain_id"] <= 0:
            raise PolicyError("Invalid lending rule chain")
        actions = value.get("allowed_actions")
        if actions != ["approve", "supply"]:
            raise PolicyError("Invalid lending actions")
        for field in ("max_amount_atomic", "max_total_fee_wei"):
            if not isinstance(value.get(field), str) or DECIMAL_RE.fullmatch(value[field]) is None:
                raise PolicyError("Invalid lending rule limit")
        digest = value.get("action_profile_digest")
        if (
            not isinstance(digest, str) or len(digest) != 64
            or any(character not in "0123456789abcdef" for character in digest)
        ):
            raise PolicyError("Invalid lending profile digest")
        if (
            value["module_id"] != "lending"
            or value["module_version"] != "1"
            or value["protocol_profile_id"] != "aave-v3-base-usdc"
            or value["protocol_profile_version"] != "1"
            or value["network"] != "base"
            or value["asset"] != "usdc"
            or value["chain_id"] != 8453
        ):
            raise PolicyError("Unsupported lending route")
        return cls(
            *(value[field] for field in identifiers), value["chain_id"],
            tuple(actions), value["max_amount_atomic"], value["max_total_fee_wei"],
            digest,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "module_id": self.module_id,
            "module_version": self.module_version,
            "protocol_profile_id": self.protocol_profile_id,
            "protocol_profile_version": self.protocol_profile_version,
            "network": self.network,
            "asset": self.asset,
            "chain_id": self.chain_id,
            "allowed_actions": list(self.allowed_actions),
            "max_amount_atomic": self.max_amount_atomic,
            "max_total_fee_wei": self.max_total_fee_wei,
            "action_profile_digest": self.action_profile_digest,
        }

@dataclass(frozen=True, slots=True)
class Policy:
    schema_version: str
    policy_version: str
    authority_enabled: bool
    transfer_rules: tuple[TransferRule, ...]
    lending_authority_enabled: bool = False
    lending_rules: tuple[LendingRule, ...] = ()

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "Policy":
        if not isinstance(value, Mapping):
            raise PolicyError("Invalid policy fields")
        schema = value.get("schema_version")
        if schema == "2":
            if set(value) != POLICY_V2_FIELDS or value.get("policy_version") != "1":
                raise PolicyError("Unsupported policy version")
            transfer_enabled = value.get("authority_enabled")
            lending_enabled = False
            raw_lending: object = []
        elif schema == "3":
            if set(value) != POLICY_V3_FIELDS or value.get("policy_version") != "2":
                raise PolicyError("Unsupported policy version")
            transfer_enabled = value.get("transfer_authority_enabled")
            lending_enabled = value.get("lending_authority_enabled")
            raw_lending = value.get("lending_rules")
        else:
            raise PolicyError("Unsupported policy version")
        if type(transfer_enabled) is not bool or type(lending_enabled) is not bool:
            raise PolicyError("Invalid authority switch")
        raw_rules = value.get("transfer_rules")
        if not isinstance(raw_rules, list) or len(raw_rules) > 64:
            raise PolicyError("Invalid transfer rules")
        rules = tuple(TransferRule.from_dict(item) for item in raw_rules)
        identities = {(rule.network, rule.asset) for rule in rules}
        if len(identities) != len(rules):
            raise PolicyError("Duplicate transfer rule")
        if not isinstance(raw_lending, list) or len(raw_lending) > 1:
            raise PolicyError("Invalid lending rules")
        lending_rules = tuple(LendingRule.from_dict(item) for item in raw_lending)
        if lending_enabled and not lending_rules:
            raise PolicyError("Enabled lending authority requires a rule")
        return cls(
            str(schema), str(value["policy_version"]), bool(transfer_enabled), rules,
            bool(lending_enabled), lending_rules,
        )

    def to_dict(self) -> dict[str, Any]:
        if self.schema_version == "2":
            if self.policy_version != "1" or self.lending_authority_enabled or self.lending_rules:
                raise PolicyError("Invalid legacy policy")
            return {
                "schema_version": "2", "policy_version": "1",
                "authority_enabled": self.authority_enabled,
                "transfer_rules": [rule.to_dict() for rule in self.transfer_rules],
            }
        if self.schema_version != "3" or self.policy_version != "2":
            raise PolicyError("Invalid policy version")
        return {
            "schema_version": "3", "policy_version": "2",
            "transfer_authority_enabled": self.authority_enabled,
            "lending_authority_enabled": self.lending_authority_enabled,
            "transfer_rules": [rule.to_dict() for rule in self.transfer_rules],
            "lending_rules": [rule.to_dict() for rule in self.lending_rules],
        }

    def disabled_v3(self, lending_rules: tuple[LendingRule, ...] | None = None) -> "Policy":
        return Policy(
            "3", "2", False, self.transfer_rules, False,
            self.lending_rules if lending_rules is None else lending_rules,
        )
