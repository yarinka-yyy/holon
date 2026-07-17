"""Default-deny evaluation for the M2.03 transfer pilot."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from holon_contracts import RefusalCode

from .model import Policy


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
        if int(payload["amount_atomic"]) > int(rule.max_amount_atomic):
            return PolicyDecision.refuse(
                RefusalCode.AMOUNT_LIMIT_EXCEEDED, "Amount exceeds the policy limit."
            )
        if int(payload["max_total_fee_wei"]) > int(rule.max_total_fee_wei):
            return PolicyDecision.refuse(
                RefusalCode.MAX_FEE_EXCEEDED, "Maximum fee exceeds the policy limit."
            )
        return PolicyDecision.allow()
