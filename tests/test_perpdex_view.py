from __future__ import annotations

from holon_wallet.perpdex_view import (
    action_presentation, funding_history_to_map, operation_history_to_map,
    result_presentation,
)


def test_funding_and_position_presentations_keep_raw_values_out_of_main_copy() -> None:
    funding = {
        "operationId": "act-1", "actionType": "FUND_TRADING_ACCOUNT", "intent": {}, "phases": [],
        "funding": {"amountAtomic": "6000000", "chainId": 42161, "maxTotalFeeWei": "1000000000000000",
                    "recipient": "0x" + "22" * 20, "tokenContract": "0x" + "11" * 20},
    }
    shown = action_presentation(funding)
    assert shown["title"] == "Deposit 6 USDC to Hyperliquid"
    assert shown["summaryRows"][0] == {"label": "Network", "value": "Arbitrum One"}
    assert all("0x" not in item["value"] for item in shown["summaryRows"])

    opened = action_presentation({
        "operationId": "act-2", "actionType": "OPEN_POSITION",
        "intent": {"market": "ETH", "notional_usdc": "12", "leverage": 2},
        "phases": [
            {"phaseType": "SET_ISOLATED_LEVERAGE", "semantic": {"is_cross": False, "leverage": 2}},
            {"phaseType": "PLACE_IOC_ORDER", "semantic": {"market": "ETH", "is_buy": True,
             "size_asset": "0.0062", "reference_price": "1887", "limit_price": "1905",
             "max_slippage_percent": "1", "reduce_only": False}},
        ],
    })
    assert opened["title"] == "Open ETH long"
    assert opened["subtitle"] == "Isolated · 2x"
    assert {item["label"] for item in opened["summaryRows"]} >= {"Margin", "Maximum position", "IOC price limit"}


def test_result_and_history_mapping_are_plain_but_keep_diagnostics_collapsed() -> None:
    action = {"operationId": "act-1", "actionType": "FUND_TRADING_ACCOUNT", "intent": {}, "phases": [],
              "funding": {"amountAtomic": "6000000", "chainId": 42161, "maxTotalFeeWei": "1",
                          "recipient": "0x" + "22" * 20, "tokenContract": "0x" + "11" * 20}}
    result = result_presentation({"actionType": "FUND_TRADING_ACCOUNT", "status": "PENDING_CREDIT",
                                  "code": "FUNDING_BROADCAST_PENDING", "phases": [],
                                  "terminalStage": "RECONCILIATION",
                                  "externalSubmissionStarted": True}, action)
    assert result["resultTitle"] == "Deposit sent"
    assert result["resultSubtitle"] == "Waiting for Hyperliquid balance update"
    assert all(item["value"] != "FUNDING_BROADCAST_PENDING" for item in result["summaryRows"])
    assert any(
        item == {"label": "Result code", "value": "FUNDING_BROADCAST_PENDING"}
        for item in result["technicalDetails"]
    )
    assert {item["label"]: item["value"] for item in result["technicalDetails"]}[
        "External submission"
    ] == "Started"

    history = operation_history_to_map({"operation_id": "act-3", "action_type": "OPEN_POSITION",
                                        "state": "COMPLETED", "created_at": "2026-08-11T12:00:00Z",
                                        "updated_at": "2026-08-11T12:00:00Z",
                                        "intent": {"market": "ETH", "side": "LONG", "notional_usdc": "12"}})
    assert history["summaryTitle"] == "Open ETH long"
    assert history["statusLabel"] == "Completed"
    assert history["isPerpDex"] is True


def test_funding_history_keeps_terminal_details_after_result_screen_closes() -> None:
    mapped = funding_history_to_map({
        "actionId": "act-evm", "operationId": "act-funding",
        "amount": "5.5 USDC", "networkLabel": "Arbitrum One",
        "sender": "0x" + "11" * 20, "recipient": "0x" + "22" * 20,
        "contract": "0x" + "33" * 20, "transactionHash": "",
        "status": "failed", "createdAt": "2026-08-12T10:00:00Z",
        "updatedAt": "2026-08-12T10:00:01Z", "dateLabel": "Aug 12, 2026",
    }, {
        "operation_id": "act-funding", "action_type": "FUND_TRADING_ACCOUNT",
        "account": "0x" + "11" * 20, "state": "FAILED",
        "created_at": "2026-08-12T10:00:00Z", "updated_at": "2026-08-12T10:00:01Z",
        "intent": {"amount_usdc": "5.5"}, "external_submission_started": False,
        "terminal_code": "FUNDING_REVALIDATION_FAILED",
        "terminal_stage": "PHASE_ARBITRUM_USDC_TRANSFER",
        "failure_category": "wallet", "phases": [{
            "phase_type": "ARBITRUM_USDC_TRANSFER", "state": "FAILED",
            "code": "FUNDING_REVALIDATION_FAILED", "public_id": None,
        }],
    })
    rows = {item["label"]: item["value"] for item in mapped["detailRows"]}
    technical = {item["label"]: item["value"] for item in mapped["technicalDetails"]}
    assert rows["Wallet"] == "0x" + "11" * 20
    assert rows["Amount"] == "5.5 USDC"
    assert rows["Signature"] == "Not created"
    assert rows["External submission"] == "Not attempted"
    assert technical["Result code"] == "FUNDING_REVALIDATION_FAILED"
    assert technical["ARBITRUM_USDC_TRANSFER"] == "FAILED · FUNDING_REVALIDATION_FAILED"
    assert "External submission started: false" in mapped["diagnosticsText"]
    assert "stopped before signing" in mapped["resultExplanation"]


def test_funding_history_does_not_confuse_local_hash_with_external_attempt() -> None:
    mapped = funding_history_to_map({
        "actionId": "act-evm", "operationId": "act-funding",
        "amount": "5.5 USDC", "sender": "0x" + "11" * 20,
        "recipient": "0x" + "22" * 20, "contract": "0x" + "33" * 20,
        "transactionHash": "0x" + "44" * 32, "status": "failed",
        "createdAt": "2026-08-12T10:00:00Z", "updatedAt": "2026-08-12T10:00:01Z",
    }, {
        "operation_id": "act-funding", "action_type": "FUND_TRADING_ACCOUNT",
        "state": "FAILED", "external_submission_started": False,
        "terminal_code": "FUNDING_CANCELLED",
        "terminal_stage": "PHASE_ARBITRUM_USDC_TRANSFER",
        "failure_category": "wallet", "phases": [],
    })
    rows = {item["label"]: item["value"] for item in mapped["detailRows"]}
    assert rows["Signature"] == "Created locally; not sent"
    assert rows["External submission"] == "Not attempted"
    assert mapped["externalSubmissionStarted"] is False


def test_price_move_result_and_history_prove_order_was_not_sent() -> None:
    action = {
        "operationId": "act-1", "actionType": "OPEN_POSITION",
        "intent": {"market": "ETH", "notional_usdc": "11", "leverage": 2},
        "phases": [{
            "phaseType": "PLACE_IOC_ORDER", "semantic": {
                "market": "ETH", "is_buy": True, "size_asset": "0.006",
                "reference_price": "1800", "limit_price": "1818",
                "max_slippage_percent": "1", "reduce_only": False,
            },
        }],
    }
    result = result_presentation({
        "actionType": "OPEN_POSITION", "status": "FAILED",
        "code": "PERPDEX_PRICE_MOVED", "phases": [],
        "terminalStage": "WALLET_EXECUTION_PRE_VERIFY",
        "externalSubmissionStarted": False,
    }, action)
    assert result["resultTitle"] == "Price changed before signing"
    assert "Order was not sent" in result["resultSubtitle"]
    details = {item["label"]: item["value"] for item in result["technicalDetails"]}
    assert details["Result code"] == "PERPDEX_PRICE_MOVED"
    assert details["External submission"] == "Not attempted"
    assert details["Signature"] == "Not created"

    history = operation_history_to_map({
        "operation_id": "act-3", "action_type": "OPEN_POSITION",
        "account": "0x" + "11" * 20, "state": "FAILED",
        "created_at": "2026-08-12T07:34:47Z", "updated_at": "2026-08-12T07:35:17Z",
        "external_submission_started": False, "terminal_code": None,
        "terminal_stage": None, "failure_category": None, "operation_class": None,
        "intent": {"market": "ETH", "side": "LONG", "notional_usdc": "11",
                   "leverage": 2, "margin_mode": "ISOLATED"},
        "phases": [
            {"phase_type": "SET_ISOLATED_LEVERAGE", "state": "PENDING", "code": None,
             "public_id": None},
            {"phase_type": "PLACE_IOC_ORDER", "state": "PENDING", "code": None,
             "public_id": None},
        ],
    }, {
        "result_code": "PERPDEX_PRICE_MOVED",
        "stage": "WALLET_EXECUTION_PRE_VERIFY",
        "failure_category": "perpdex_state", "recovery_state": "NOT_REQUIRED",
    })
    assert history["statusLabel"] == "Failed · price changed before signing"
    assert history["externalSubmissionStarted"] is False
    assert "Result code: PERPDEX_PRICE_MOVED" in history["diagnosticsText"]
    assert "Wallet: 0x1111111111111111111111111111111111111111" in history["diagnosticsText"]
    assert "PLACE_IOC_ORDER: PENDING" in history["diagnosticsText"]
    assert "External submission started: false" in history["diagnosticsText"]


def test_ambiguous_history_explains_completed_recovery_without_resuming_action() -> None:
    history = operation_history_to_map({
        "operation_id": "act-4", "action_type": "OPEN_POSITION",
        "account": "0x" + "11" * 20, "state": "FAILED",
        "created_at": "2026-08-12T07:37:59Z", "updated_at": "2026-08-12T07:38:19Z",
        "external_submission_started": False, "terminal_code": None,
        "terminal_stage": None, "failure_category": None, "operation_class": None,
        "intent": {"market": "ETH", "side": "LONG", "notional_usdc": "11",
                   "leverage": 2, "margin_mode": "ISOLATED"}, "phases": [],
    }, {
        "result_code": "WALLET_PREPARATION_AMBIGUOUS", "stage": "WALLET_PREPARE",
        "failure_category": "wallet_ipc", "ipc_outcome": "WALLET_RESPONSE_SCHEMA_INVALID",
        "recovery_state": "COMPLETED",
    })
    assert "response could not be safely validated" in history["resultExplanation"]
    assert "original action was not resumed" in history["resultExplanation"]
    assert any(
        item == {"label": "Recovery", "value": "Completed · original action not resumed"}
        for item in history["detailRows"]
    )
