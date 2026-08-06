"""Declarative field sets for contract schema version 1."""

from .model import MessageKind

BASE_FIELDS = frozenset({"schema_version", "request_id", "kind", "timestamp", "payload"})
ACTION_FIELDS = BASE_FIELDS | {"action_id"}

ACTION_REQUIRED_KINDS = frozenset(
    {
        MessageKind.PREPARE_TRANSFER,
        MessageKind.TRANSFER_INTENT,
        MessageKind.LENDING_AUTHORITY_INTENT,
        MessageKind.ACTION_STATUS_REQUEST,
        MessageKind.CANCEL_ACTION,
        MessageKind.RECOVER_ACTION,
        MessageKind.PROTECTED_FLOW_STARTED,
        MessageKind.ACTION_STATUS,
        MessageKind.RECOVERY_REQUIRED,
    }
)

ACTION_OPTIONAL_KINDS = frozenset(
    {MessageKind.REFUSAL, MessageKind.ERROR, MessageKind.SIGNING_DISABLED}
)

REQUEST_KINDS = frozenset(
    {
        MessageKind.HEALTH_REQUEST,
        MessageKind.OPEN_WALLET,
        MessageKind.READ_WALLET_BALANCES,
        MessageKind.READ_LENDING_MARKETS,
        MessageKind.READ_LENDING_POSITIONS,
        MessageKind.READ_LENDING_PORTFOLIO,
        MessageKind.READ_EARN_PORTFOLIO,
        MessageKind.MODULE_READ_REQUEST,
        MessageKind.LENDING_ACTION_INTENT,
        MessageKind.LENDING_AUTHORITY_INTENT,
        MessageKind.PREPARE_TRANSFER,
        MessageKind.TRANSFER_INTENT,
        MessageKind.ACTION_STATUS_REQUEST,
        MessageKind.CANCEL_ACTION,
        MessageKind.RECOVER_ACTION,
    }
)

PAYLOAD_FIELDS = {
    MessageKind.HEALTH_REQUEST: frozenset(),
    MessageKind.OPEN_WALLET: frozenset(),
    MessageKind.READ_WALLET_BALANCES: frozenset(),
    MessageKind.READ_LENDING_MARKETS: frozenset(),
    MessageKind.READ_LENDING_POSITIONS: frozenset(),
    MessageKind.READ_LENDING_PORTFOLIO: frozenset(),
    MessageKind.READ_EARN_PORTFOLIO: frozenset(),
    MessageKind.MODULE_READ_REQUEST: frozenset(
        {"module_id", "capability_id", "operation", "params"}
    ),
    MessageKind.LENDING_ACTION_INTENT: frozenset(
        {
            "module_id", "module_version", "protocol_profile_id",
            "protocol_profile_version", "network", "asset",
            "beneficiary_mode", "action", "amount_mode", "amount",
        }
    ),
    MessageKind.LENDING_AUTHORITY_INTENT: frozenset(
        {
            "module_id", "module_version", "protocol_profile_id",
            "protocol_profile_version", "network", "asset",
            "beneficiary_mode", "action", "amount_mode", "amount",
        }
    ),
    MessageKind.TRANSFER_INTENT: frozenset(
        {"network", "asset", "amount", "recipient"}
    ),
    MessageKind.PREPARE_TRANSFER: frozenset(
        {
            "policy_version",
            "action_type",
            "network",
            "asset",
            "amount_atomic",
            "recipient",
            "max_total_fee_wei",
        }
    ),
    MessageKind.ACTION_STATUS_REQUEST: frozenset(),
    MessageKind.CANCEL_ACTION: frozenset(),
    MessageKind.RECOVER_ACTION: frozenset(),
    MessageKind.HEALTH_RESPONSE: frozenset(
        {"guard_state", "authority_available", "code", "message", "compatibility"}
    ),
    MessageKind.WALLET_OPENED: frozenset(
        {"guard_state", "authority_available", "wallet_state", "code", "message"}
    ),
    MessageKind.WALLET_BALANCES: frozenset(
        {
            "balance_schema_version", "status", "authority_available", "account",
            "networks", "code", "message",
        }
    ),
    MessageKind.LENDING_MARKETS: frozenset(
        {
            "status", "authority_available", "network", "asset", "markets",
            "highest_observed", "recommendation", "delivery", "code", "message",
        }
    ),
    MessageKind.LENDING_POSITIONS: frozenset(
        {
            "status", "authority_available", "account", "network", "asset",
            "positions", "code", "message",
        }
    ),
    MessageKind.LENDING_PORTFOLIO: frozenset(
        {
            "status", "authority_available", "account", "network", "asset",
            "summary", "protocols", "recommendation", "delivery", "history",
            "code", "message",
        }
    ),
    MessageKind.EARN_PORTFOLIO: frozenset(
        {
            "account", "authority_available", "code", "earn_schema_version",
            "message", "providers", "status", "total_complete",
        }
    ),
    MessageKind.MODULE_READ_RESPONSE: frozenset(
        {
            "status", "module_id", "capability_id", "operation", "result",
            "code", "message",
        }
    ),
    MessageKind.LENDING_ACTION_PREVIEW: frozenset(
        {
            "status", "authority_available", "execution_available", "account",
            "requested_action", "next_action", "protocol", "profile_id",
            "profile_version", "profile_digest", "network", "asset",
            "amount_mode", "amount_atomic", "display_amount", "target", "method",
            "call_amount_atomic",
            "calldata_hash", "native_value_wei", "nonce", "gas",
            "max_fee_per_gas_wei", "max_priority_fee_per_gas_wei",
            "l2_fee_ceiling_wei", "l1_fee_upper_bound_wei",
            "max_total_fee_wei", "block_number", "observed_at", "expires_at",
            "preview_digest", "checks", "caveats", "code", "message",
            "position_before_atomic",
        }
    ),
    MessageKind.PROTECTED_FLOW_STARTED: frozenset(
        {"guard_state", "action_state", "flow_id", "code", "message"}
    ),
    MessageKind.ACTION_STATUS: frozenset(
        {"guard_state", "action_state", "flow_id", "code", "message"}
    ),
    MessageKind.REFUSAL: frozenset({"code", "message", "retryable"}),
    MessageKind.ERROR: frozenset({"code", "message", "retryable"}),
    MessageKind.RECOVERY_REQUIRED: frozenset(
        {"guard_state", "action_state", "flow_id", "code", "message"}
    ),
    MessageKind.SIGNING_DISABLED: frozenset(
        {"guard_state", "authority_available", "code", "message"}
    ),
    MessageKind.COMPATIBILITY_STATUS: frozenset(
        {"guard_state", "authority_available", "code", "message", "supported_schema_versions", "policy_version"}
    ),
}
