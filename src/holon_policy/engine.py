"""Default-deny evaluation for the M2.03 transfer pilot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from holon_contracts import RefusalCode
from holon_lending.action_profiles import (
    ACTION_PROFILE_DIGESTS, AAVE_MAX_TOTAL_FEE_WEI,
)

from .model import LendingRule, Policy, RecipientRule, TransferRule


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    allowed: bool
    code: str
    message: str

    @classmethod
    def allow(cls) -> "PolicyDecision":
        return cls(True, "POLICY_ALLOWED", "Transfer is allowed by policy.")

    @classmethod
    def refuse(cls, code: RefusalCode, message: str) -> "PolicyDecision":
        return cls(False, code.value, message)


class PolicyEngine:
    def __init__(self, policy: Policy) -> None:
        self.policy = policy

    def evaluate_transfer(self, payload: Mapping[str, Any]) -> PolicyDecision:
        if not self.policy.authority_enabled:
            return PolicyDecision.refuse(
                RefusalCode.POLICY_AUTHORITY_DISABLED, "Wallet authority is disabled by policy."
            )
        if payload.get("policy_version") != self.policy.policy_version:
            return PolicyDecision.refuse(
                RefusalCode.POLICY_VERSION_MISMATCH, "Policy version does not match."
            )
        if payload.get("action_type") != "transfer":
            return PolicyDecision.refuse(RefusalCode.ACTION_NOT_ALLOWED, "Action is not allowed.")
        network = payload.get("network")
        asset = payload.get("asset")
        network_rules = [rule for rule in self.policy.transfer_rules if rule.network == network]
        if not network_rules:
            return PolicyDecision.refuse(
                RefusalCode.NETWORK_NOT_ALLOWED, "Network is not allowed."
            )
        rule = next((item for item in network_rules if item.asset == asset), None)
        if rule is None:
            return PolicyDecision.refuse(RefusalCode.ASSET_NOT_ALLOWED, "Asset is not allowed.")
        limits = self._evaluate_limits(
            rule, int(payload["amount_atomic"]), payload.get("recipient")
        )
        if not limits.allowed:
            return limits
        if int(payload["max_total_fee_wei"]) > int(rule.max_total_fee_wei):
            return PolicyDecision.refuse(
                RefusalCode.MAX_FEE_EXCEEDED, "Maximum fee exceeds the policy limit."
            )
        return PolicyDecision.allow()

    def evaluate_intent(self, network: object, asset: object, amount_atomic: int,
                        recipient: object) -> tuple[PolicyDecision, TransferRule | None]:
        if not self.policy.authority_enabled:
            return PolicyDecision.refuse(
                RefusalCode.POLICY_AUTHORITY_DISABLED,
                "Wallet authority is disabled by policy.",
            ), None
        network_rules = [
            rule for rule in self.policy.transfer_rules if rule.network == network
        ]
        if not network_rules:
            return PolicyDecision.refuse(
                RefusalCode.NETWORK_NOT_ALLOWED, "Network is not allowed."
            ), None
        rule = next((item for item in network_rules if item.asset == asset), None)
        if rule is None:
            return PolicyDecision.refuse(
                RefusalCode.ASSET_NOT_ALLOWED, "Asset is not allowed."
            ), None
        limits = self._evaluate_limits(rule, amount_atomic, recipient)
        return limits, rule if limits.allowed else None

    def evaluate_lending_intent(
        self, payload: Mapping[str, Any], action_profile_digest: str,
    ) -> tuple[PolicyDecision, LendingRule | None]:
        if self.policy.schema_version == "4":
            fixed = {
                "module_id": "lending", "network": "base", "asset": "usdc",
            }
            profile_id = payload.get("protocol_profile_id")
            if (
                not isinstance(profile_id, str)
                or ACTION_PROFILE_DIGESTS.get(profile_id) != action_profile_digest
                or any(payload.get(name) != expected for name, expected in fixed.items())
            ):
                return PolicyDecision.refuse(
                    RefusalCode.ACTION_NOT_ALLOWED, "Lending profile is not allowed.",
                ), None
            action, mode = payload.get("action"), payload.get("amount_mode")
            if action not in {"supply", "withdraw"} or mode not in {"exact", "all"}:
                return PolicyDecision.refuse(
                    RefusalCode.ACTION_NOT_ALLOWED, "Lending action is not allowed.",
                ), None
            amount = payload.get("amount_atomic")
            if mode == "all" and amount is None:
                return PolicyDecision.allow(), None
            if type(amount) is not int or amount <= 0:
                return PolicyDecision.refuse(
                    RefusalCode.AMOUNT_INVALID, "Lending amount is invalid.",
                ), None
            return PolicyDecision.allow(), None
        if not self.policy.lending_authority_enabled:
            return PolicyDecision.refuse(
                RefusalCode.POLICY_AUTHORITY_DISABLED,
                "Lending authority is disabled by policy.",
            ), None
        rule = next((item for item in self.policy.lending_rules if (
            item.module_id == payload.get("module_id")
            and item.protocol_profile_id == payload.get("protocol_profile_id")
            and item.network == payload.get("network")
            and item.asset == payload.get("asset")
        )), None)
        if rule is None or rule.action_profile_digest != action_profile_digest:
            return PolicyDecision.refuse(
                RefusalCode.ACTION_NOT_ALLOWED, "Lending profile is not allowed.",
            ), None
        action = payload.get("action")
        mode = payload.get("amount_mode")
        action_allowed = (
            action == "supply"
            and mode == "exact"
            and rule.allowed_actions == ("approve", "supply")
        ) or (
            action == "withdraw"
            and mode in {"exact", "all"}
            and rule.allowed_actions == ("withdraw",)
        )
        if not action_allowed:
            return PolicyDecision.refuse(
                RefusalCode.ACTION_NOT_ALLOWED, "Lending action is not allowed.",
            ), None
        amount = payload.get("amount_atomic")
        if mode == "all" and amount is None:
            return PolicyDecision.allow(), rule
        if type(amount) is not int or amount <= 0:
            return PolicyDecision.refuse(
                RefusalCode.AMOUNT_INVALID, "Lending amount is invalid.",
            ), None
        if amount > int(rule.max_amount_atomic):
            return PolicyDecision.refuse(
                RefusalCode.AMOUNT_LIMIT_EXCEEDED,
                "Lending amount exceeds the policy limit.",
            ), None
        return PolicyDecision.allow(), rule

    @staticmethod
    def evaluate_lending_prepared(
        next_action: object, amount_atomic: object, max_total_fee_wei: object,
        rule: LendingRule | None,
    ) -> PolicyDecision:
        if rule is None:
            if next_action not in {"approve", "supply", "deposit", "withdraw", "redeem"}:
                return PolicyDecision.refuse(
                    RefusalCode.ACTION_NOT_ALLOWED, "Prepared lending action is not allowed.",
                )
            if type(amount_atomic) is not int or amount_atomic <= 0:
                return PolicyDecision.refuse(RefusalCode.AMOUNT_INVALID, "Amount is invalid.")
            if type(max_total_fee_wei) is not int or max_total_fee_wei <= 0:
                return PolicyDecision.refuse(
                    RefusalCode.MAX_FEE_REQUIRED, "Maximum fee is required.",
                )
            if max_total_fee_wei > AAVE_MAX_TOTAL_FEE_WEI:
                return PolicyDecision.refuse(
                    RefusalCode.MAX_FEE_EXCEEDED, "Maximum fee exceeds the built-in limit.",
                )
            return PolicyDecision.allow()
        if next_action not in rule.allowed_actions:
            return PolicyDecision.refuse(
                RefusalCode.ACTION_NOT_ALLOWED, "Prepared lending action is not allowed.",
            )
        if type(amount_atomic) is not int or amount_atomic <= 0:
            return PolicyDecision.refuse(RefusalCode.AMOUNT_INVALID, "Amount is invalid.")
        if amount_atomic > int(rule.max_amount_atomic):
            return PolicyDecision.refuse(
                RefusalCode.AMOUNT_LIMIT_EXCEEDED, "Amount exceeds the policy limit.",
            )
        if type(max_total_fee_wei) is not int or max_total_fee_wei <= 0:
            return PolicyDecision.refuse(
                RefusalCode.MAX_FEE_REQUIRED, "Maximum fee is required.",
            )
        if max_total_fee_wei > int(rule.max_total_fee_wei):
            return PolicyDecision.refuse(
                RefusalCode.MAX_FEE_EXCEEDED, "Maximum fee exceeds the policy limit.",
            )
        return PolicyDecision.allow()

    @classmethod
    def _evaluate_limits(cls, rule: TransferRule, amount: object,
                         recipient: object) -> PolicyDecision:
        if type(amount) is not int or amount <= 0:
            return PolicyDecision.refuse(RefusalCode.AMOUNT_INVALID, "Amount is invalid.")
        recipient_rule = cls._recipient(rule, recipient)
        if recipient_rule is None:
            return PolicyDecision.refuse(
                RefusalCode.RECIPIENT_NOT_ALLOWED, "Recipient is not allowed."
            )
        limit = min(int(rule.max_amount_atomic), int(recipient_rule.max_amount_atomic))
        if amount > limit:
            return PolicyDecision.refuse(
                RefusalCode.AMOUNT_LIMIT_EXCEEDED, "Amount exceeds the policy limit."
            )
        return PolicyDecision.allow()

    @staticmethod
    def _recipient(rule: TransferRule, value: object) -> RecipientRule | None:
        if not isinstance(value, str):
            return None
        normalized = value.lower()
        return next((item for item in rule.recipients if item.address == normalized), None)

    @staticmethod
    def evaluate_prepared_fee(max_total_fee_wei: int,
                              rule: TransferRule) -> PolicyDecision:
        if type(max_total_fee_wei) is not int or max_total_fee_wei <= 0:
            return PolicyDecision.refuse(
                RefusalCode.MAX_FEE_REQUIRED, "Maximum fee is required."
            )
        if max_total_fee_wei > int(rule.max_total_fee_wei):
            return PolicyDecision.refuse(
                RefusalCode.MAX_FEE_EXCEEDED,
                "Maximum fee exceeds the policy limit.",
            )
        return PolicyDecision.allow()
