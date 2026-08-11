from __future__ import annotations

import json
from pathlib import Path
import sys

import pytest

from holon_contracts import ContractViolation, MessageKind, make_envelope, new_action_id

ROOT = Path(__file__).parents[1]
PERPDEX_SRC = ROOT / "modules" / "perpdex" / "src"
sys.path.insert(0, str(PERPDEX_SRC))

from holon_perpdex import (  # noqa: E402
    ActionType,
    AmountMode,
    ContractError,
    PROFILE_DIGEST,
    PerpDexActionIntent,
    PositionSide,
)


def test_perpdex_intents_are_closed_decimal_string_contracts() -> None:
    opened = PerpDexActionIntent.from_mapping("OPEN_POSITION", {
        "leverage": 2, "margin_mode": "ISOLATED", "market": "BTC",
        "notional_usdc": "1000000", "side": "LONG",
    })
    assert opened.action_type is ActionType.OPEN_POSITION
    assert opened.side is PositionSide.LONG
    assert opened.is_entry
    assert opened.review_seconds == 90

    defaulted = PerpDexActionIntent.from_mapping("OPEN_POSITION", {
        "leverage": 2, "market": "ETH", "notional_usdc": "6", "side": "LONG",
    })
    assert defaulted.to_mapping()["margin_mode"] == "ISOLATED"

    partial = PerpDexActionIntent.from_mapping("CLOSE_POSITION", {
        "amount_mode": "PERCENT", "market": "ETH", "percent": "25.5",
    })
    assert partial.amount_mode is AmountMode.PERCENT
    assert not partial.is_entry

    deposit = PerpDexActionIntent.from_mapping("HLP_DEPOSIT", {"amount_usdc": "10.25"})
    assert deposit.review_seconds == 300
    funding = PerpDexActionIntent.from_mapping(
        "FUND_TRADING_ACCOUNT", {"amount_usdc": "5.000001"},
    )
    assert funding.action_type is ActionType.FUND_TRADING_ACCOUNT
    assert funding.is_entry and funding.review_seconds == 300
    withdrawal = PerpDexActionIntent.from_mapping(
        "HLP_WITHDRAW", {"amount_mode": "ALL", "amount_usdc": None},
    )
    assert withdrawal.amount_mode is AmountMode.ALL


@pytest.mark.parametrize(
    ("action", "params"),
    [
        ("OPEN_POSITION", {"leverage": 0, "margin_mode": "ISOLATED", "market": "BTC", "notional_usdc": "10", "side": "LONG"}),
        ("OPEN_POSITION", {"leverage": 2, "margin_mode": "ISOLATED", "market": "DOGE", "notional_usdc": "10", "side": "LONG"}),
        ("OPEN_POSITION", {"leverage": 2, "margin_mode": "ISOLATED", "market": "BTC", "notional_usdc": "10.0000001", "side": "LONG"}),
        ("OPEN_POSITION", {"leverage": 2, "margin_mode": "ISOLATED", "market": "BTC", "notional_usdc": 10.0, "side": "LONG"}),
        ("OPEN_POSITION", {"leverage": 2, "margin_mode": "UNIFIED", "market": "BTC", "notional_usdc": "10", "side": "LONG"}),
        ("CLOSE_POSITION", {"amount_mode": "PERCENT", "market": "SOL", "percent": "100"}),
        ("CLOSE_POSITION", {"amount_mode": "FULL", "market": "SOL", "percent": "50"}),
        ("HLP_DEPOSIT", {"amount_usdc": "10.0000001"}),
        ("FUND_TRADING_ACCOUNT", {"amount_usdc": "4.999999"}),
        ("FUND_TRADING_ACCOUNT", {"amount_usdc": "5.0000001"}),
        ("FUND_TRADING_ACCOUNT", {"amount_usdc": 5.0}),
        ("FUND_TRADING_ACCOUNT", {"amount_usdc": "5", "asset": "USDC.e"}),
        ("HLP_WITHDRAW", {"amount_mode": "ALL", "amount_usdc": "1"}),
        ("HLP_WITHDRAW", {"amount_mode": "EXACT", "amount_usdc": None}),
    ],
)
def test_perpdex_intents_refuse_unsafe_or_ambiguous_values(action, params) -> None:
    with pytest.raises(ContractError):
        PerpDexActionIntent.from_mapping(action, params)


def test_shared_module_action_envelopes_are_strict_and_secret_free() -> None:
    params = {
        "leverage": 2, "margin_mode": "ISOLATED", "market": "BTC",
        "notional_usdc": "25", "side": "SHORT",
    }
    preview = make_envelope(MessageKind.MODULE_ACTION_INTENT, {
        "action_type": "OPEN_POSITION",
        "capability_id": "holon.perpdex.action.guard",
        "module_id": "holon.perpdex",
        "params": params,
    })
    assert preview.action_id is None
    authority = make_envelope(
        MessageKind.MODULE_AUTHORITY_INTENT,
        {
            "action_type": "OPEN_POSITION",
            "capability_id": "holon.perpdex.action.guard",
            "module_id": "holon.perpdex",
            "params": params,
            "preview_digest": PROFILE_DIGEST,
        },
        action_id=new_action_id(),
    )
    assert authority.action_id is not None

    for bad in (
        {**params, "calldata": "0x00"},
        {**params, "notional_usdc": 25.0},
        {**params, "private_key": "forbidden"},
    ):
        with pytest.raises(ContractViolation):
            make_envelope(MessageKind.MODULE_ACTION_INTENT, {
                "action_type": "OPEN_POSITION",
                "capability_id": "holon.perpdex.action.guard",
                "module_id": "holon.perpdex",
                "params": bad,
            })


def test_shared_module_action_preview_has_exact_safe_shape() -> None:
    payload = {
        "account": {"address": "0x" + "12" * 20, "label": "Main"},
        "action_type": "OPEN_POSITION",
        "authority_available": False,
        "capability_id": "holon.perpdex.action.guard",
        "caveats": [],
        "checks": ["MARKET_VERIFIED"],
        "code": "MODULE_ACTION_PREVIEW_READY",
        "execution_available": False,
        "expires_at": "2026-08-06T12:01:30Z",
        "message": "PerpDEX action preview is ready.",
        "module_id": "holon.perpdex",
        "preview": {"market": "BTC", "notional_usdc": "25"},
        "preview_digest": "a" * 64,
        "status": "PREVIEW_READY",
    }
    envelope = make_envelope(MessageKind.MODULE_ACTION_PREVIEW, payload)
    assert json.dumps(envelope.payload, sort_keys=True)

    with pytest.raises(ContractViolation):
        make_envelope(MessageKind.MODULE_ACTION_PREVIEW, {
            **payload, "preview": {"market": "BTC", "wire_payload": "0x00"},
        })
