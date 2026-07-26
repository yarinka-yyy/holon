"""Fresh, authority-bearing Aave action construction from semantic intent."""

from __future__ import annotations

from datetime import UTC, datetime

from holon_lending import AAVE_SAFETY_DIGEST, ActionProfilesState
from holon_lending.preflight import (
    MAX_UINT256, LendingPreflightError, LendingPreflightService, encode_approve,
    encode_supply, encode_withdraw, parse_lending_intent,
)

from .model import ProfileSummary
from .transfer import (
    ACTION_LIFETIME, TRANSFER_SCHEMA_VERSION, PreparedTransferAction,
    UnsignedTransaction,
)


def prepare_lending_action(
    service: LendingPreflightService,
    profiles: ActionProfilesState,
    profile: ProfileSummary,
    request: dict[str, object],
) -> PreparedTransferAction:
    """Repeat the complete preflight and bind its exact transaction to a new action."""
    action_profile = profiles.profile
    if action_profile is None or action_profile.digest != request.get("action_profile_digest"):
        raise LendingPreflightError("ACTION_PROFILE_UNAVAILABLE")
    raw_intent = {
        "module_id": "lending", "module_version": "1",
        "protocol_profile_id": "aave-v3-base-usdc",
        "protocol_profile_version": "1", "network": "base", "asset": "usdc",
        "beneficiary_mode": "active_wallet_account", "action": request["action"],
        "amount_mode": request["amount_mode"], "amount": request["amount"],
    }
    intent = parse_lending_intent(raw_intent)
    raw_resolved = request.get("resolved_amount_atomic")
    resolved_amount = (
        int(str(raw_resolved)) if raw_resolved is not None
        else intent.amount_atomic
    )
    if resolved_amount is not None and (
        resolved_amount <= 0
        or intent.amount_mode == "exact" and intent.amount_atomic != resolved_amount
    ):
        raise LendingPreflightError("LENDING_AMOUNT_MISMATCH")
    preview = service.prepare(
        raw_intent, {"label": profile.label, "address": profile.address},
        expected_profile_digest=action_profile.digest,
        frozen_amount_atomic=resolved_amount,
    )
    if preview.get("status") != "PREVIEW_READY":
        raise LendingPreflightError(str(preview.get("code", "LENDING_ACTION_UNAVAILABLE")))
    amount = int(str(preview["amount_atomic"]))
    if resolved_amount is not None and amount != resolved_amount:
        raise LendingPreflightError("LENDING_AMOUNT_MISMATCH")
    next_action = str(preview["next_action"])
    if next_action == "approve":
        calldata = encode_approve(action_profile.pool, amount)
    elif next_action == "supply":
        calldata = encode_supply(action_profile.asset, amount, profile.address)
    elif next_action == "withdraw":
        calldata = encode_withdraw(
            action_profile.asset,
            MAX_UINT256 if intent.amount_mode == "all" else amount,
            profile.address,
        )
    else:
        raise LendingPreflightError("LENDING_ACTION_UNAVAILABLE")
    target = action_profile.asset if next_action == "approve" else action_profile.pool
    created = datetime.fromisoformat(str(request["created_at"]).replace("Z", "+00:00")).astimezone(UTC)
    expires = datetime.fromisoformat(str(request["expires_at"]).replace("Z", "+00:00")).astimezone(UTC)
    if expires - created != ACTION_LIFETIME or datetime.now(UTC) >= expires:
        raise LendingPreflightError("ACTION_EXPIRED")
    tx = UnsignedTransaction(
        2, action_profile.chain_id, int(str(preview["nonce"])), target, 0, calldata,
        int(str(preview["gas"])),
        int(str(preview["l2_fee_ceiling_wei"])) // int(str(preview["gas"])),
        int(str(preview["max_priority_fee_per_gas_wei"])),
    )
    return PreparedTransferAction(
        TRANSFER_SCHEMA_VERSION, str(request["action_id"]), profile.profile_id,
        profile.label, profile.address, action_profile.pool, "base", "Base",
        action_profile.chain_id, "usdc", "USDC", action_profile.asset, amount, 6,
        tx, int(str(preview["block_number"])), int(str(preview["max_total_fee_wei"])),
        created, expires, False, int(request["policy_revision"]),
        str(request["policy_digest"]), "lending", next_action,
        action_profile.digest, int(str(preview["l2_fee_ceiling_wei"])),
        int(str(preview["l1_fee_upper_bound_wei"])),
        intent.amount_mode,
        str(request.get("operation_id", request["action_id"])),
        str(request.get("phase_action_id", request["action_id"])),
        str(request.get("phase", "withdraw" if intent.action == "withdraw" else "approve_or_supply")),
        AAVE_SAFETY_DIGEST,
        int(str(preview.get("position_before_atomic", "0"))),
    )
