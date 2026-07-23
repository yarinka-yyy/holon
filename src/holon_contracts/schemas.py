"""Declarative field sets for contract schema version 1."""

from .model import MessageKind

BASE_FIELDS = frozenset({"schema_version", "request_id", "kind", "timestamp", "payload"})
ACTION_FIELDS = BASE_FIELDS | {"action_id"}

ACTION_REQUIRED_KINDS = frozenset(
    {
        MessageKind.PREPARE_TRANSFER,
        MessageKind.TRANSFER_INTENT,
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
        {"status", "authority_available", "account", "networks", "code", "message"}
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
