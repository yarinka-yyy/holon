"""Deterministic descriptions; journal callers cannot supply free text."""

from __future__ import annotations

from typing import Any, Mapping

from .model import EventType

TEMPLATES = {
    EventType.POLICY_DECISION: "Policy decision: {policy_result}.",
    EventType.REFUSAL: "Authority request was refused ({code}).",
    EventType.FLOW_STARTED: "Protected Wallet flow started.",
    EventType.ACTION_CANCELLED: "Protected action was cancelled.",
    EventType.RECOVERY_REQUIRED: "Protected flow requires recovery.",
    EventType.RECOVERY_COMPLETED: "Protected-flow recovery completed.",
    EventType.SIGNING_DISABLED: "Wallet authority was disabled ({code}).",
    EventType.TECHNICAL_ERROR: "A security component reported {code}.",
    EventType.REQUEST_BLOCK_STARTED: "Repeated requests temporarily blocked authority.",
    EventType.REQUEST_BLOCK_EXPIRED: "Repeated-request block expired.",
    EventType.REQUEST_BLOCK_CLEARED: "Wallet cleared the repeated-request block.",
    EventType.LOCAL_APPROVED: "Wallet approved the exact local action.",
    EventType.LOCAL_REJECTED: "Wallet rejected the local action.",
    EventType.BROADCAST_RESULT: "Wallet recorded a broadcast result ({code}).",
    EventType.CONTRACT_ACTION: "Wallet recorded a bounded contract action.",
}


def description_for(event_type: EventType, code: str, fields: Mapping[str, Any]) -> str:
    values = {"code": code, "policy_result": fields.get("policy_result", "UNKNOWN")}
    return TEMPLATES[event_type].format_map(values)
