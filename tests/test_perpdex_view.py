from __future__ import annotations

from holon_wallet.perpdex_view import action_presentation, operation_history_to_map, result_presentation


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
                                  "code": "FUNDING_BROADCAST_PENDING", "phases": []}, action)
    assert result["resultTitle"] == "Deposit sent"
    assert result["resultSubtitle"] == "Waiting for Hyperliquid balance update"
    assert all(item["value"] != "FUNDING_BROADCAST_PENDING" for item in result["summaryRows"])
    assert result["technicalDetails"][-1]["value"] == "FUNDING_BROADCAST_PENDING"

    history = operation_history_to_map({"operation_id": "act-3", "action_type": "OPEN_POSITION",
                                        "state": "COMPLETED", "created_at": "2026-08-11T12:00:00Z",
                                        "updated_at": "2026-08-11T12:00:00Z",
                                        "intent": {"market": "ETH", "side": "LONG", "notional_usdc": "12"}})
    assert history["summaryTitle"] == "Open ETH long"
    assert history["statusLabel"] == "Completed"
    assert history["isPerpDex"] is True
