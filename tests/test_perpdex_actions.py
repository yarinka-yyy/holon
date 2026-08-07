from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
import json
import sys
import uuid

import pytest

ROOT = Path(__file__).parents[1]
PERPDEX_SRC = ROOT / "modules" / "perpdex" / "src"
sys.path.insert(0, str(PERPDEX_SRC))

from holon_perpdex.actions import (  # noqa: E402
    AdapterError, HyperliquidActionBuilder, phase_action,
)
from holon_perpdex.contracts import (  # noqa: E402
    ContractError, PhaseType, ProtectedActionBundle,
)
from holon_perpdex.guard import GuardProtectedActionAdapter  # noqa: E402
from holon_perpdex.persistence import (  # noqa: E402
    PerpDexNonceStore, PerpDexOperationStore, PersistenceError,
)
from holon_perpdex.profile import HLP_ADDRESS, HLP_NAME  # noqa: E402
from holon_perpdex.reader import HyperliquidReader  # noqa: E402

ACCOUNT = {"address": "0x" + "12" * 20, "label": "Main"}
CLOCK = 1_786_000_000.0


class ActionInfo:
    def __init__(
        self, *, position: str | None = None, orders: tuple[int, ...] = (),
        referred_by=None, withdrawable: str = "200", hlp_equity: str = "20",
        lockup: int = 0,
    ) -> None:
        self.position = position
        self.orders = orders
        self.referred_by = referred_by
        self.withdrawable = withdrawable
        self.hlp_equity = hlp_equity
        self.lockup = lockup
        self.ledger_updates: list[dict[str, object]] = []
        self.order_status: object = {"status": "unknownOid"}
        self.fills: list[dict[str, object]] = []
        self.calls: list[dict[str, object]] = []
        self.prices = {
            "BTC": ("59999", "60001"), "ETH": ("2999", "3001"),
            "SOL": ("149", "151"),
        }
        self.max_leverages = {"BTC": 40, "ETH": 25, "SOL": 20}

    def __call__(self, payload: Mapping[str, object]) -> object:
        self.calls.append(dict(payload))
        kind = payload["type"]
        if kind == "metaAndAssetCtxs":
            return [
                {"universe": [
                    {"maxLeverage": self.max_leverages["BTC"], "name": "BTC", "szDecimals": 5},
                    {"maxLeverage": self.max_leverages["ETH"], "name": "ETH", "szDecimals": 4},
                    {"maxLeverage": self.max_leverages["SOL"], "name": "SOL", "szDecimals": 2},
                ]},
                [
                    {"funding": "0.00001", "markPx": "60000", "openInterest": "10", "oraclePx": "60000"},
                    {"funding": "0.00002", "markPx": "3000", "openInterest": "20", "oraclePx": "3000"},
                    {"funding": "-0.00001", "markPx": "150", "openInterest": "30", "oraclePx": "150"},
                ],
            ]
        if kind == "l2Book":
            bid, ask = self.prices[str(payload["coin"])]
            return {
                "coin": payload["coin"], "levels": [[{"px": bid}], [{"px": ask}]],
                "time": int(CLOCK * 1000),
            }
        if kind == "clearinghouseState":
            positions = []
            if self.position is not None:
                size = self.position
                positions.append({"position": {
                    "coin": "BTC", "entryPx": "60000",
                    "leverage": {"type": "isolated", "value": 2},
                    "liquidationPx": "30000", "marginUsed": "25",
                    "positionValue": "60", "szi": size, "unrealizedPnl": "1",
                }})
            return {
                "assetPositions": positions,
                "marginSummary": {
                    "accountValue": "250", "totalMarginUsed": "25" if positions else "0",
                    "totalNtlPos": "60" if positions else "0",
                },
                "withdrawable": self.withdrawable,
            }
        if kind == "frontendOpenOrders":
            return [{
                "coin": "BTC", "limitPx": "61000", "oid": oid,
                "orderType": "Limit", "reduceOnly": True, "side": "Sell",
                "sz": "0.001", "timestamp": int(CLOCK * 1000),
            } for oid in self.orders]
        if kind == "userFees":
            return {"userAddRate": "0.00015", "userCrossRate": "0.00045"}
        if kind == "referral":
            return {"cumVlm": "999999", "referredBy": self.referred_by}
        if kind == "vaultDetails":
            return {
                "allowDeposits": True, "apr": "0.12",
                "followerState": {
                    "allTimePnl": "3", "lockupUntil": self.lockup, "pnl": "1",
                    "vaultEquity": self.hlp_equity,
                },
                "isClosed": False, "name": HLP_NAME,
                "relationship": {"data": {"childAddresses": []}, "type": "parent"},
                "vaultAddress": HLP_ADDRESS,
            }
        if kind == "userVaultEquities":
            return [{"equity": self.hlp_equity, "vaultAddress": HLP_ADDRESS}]
        if kind == "userNonFundingLedgerUpdates":
            return list(self.ledger_updates)
        if kind == "orderStatus":
            return self.order_status
        if kind == "userFillsByTime":
            return list(self.fills)
        raise AssertionError(kind)


def builder(tmp_path: Path, info: ActionInfo) -> HyperliquidActionBuilder:
    return HyperliquidActionBuilder(
        HyperliquidReader(info), clock=lambda: CLOCK,
        nonce_store=PerpDexNonceStore(
            tmp_path / "perpdex-nonces.json", clock_ms=lambda: int(CLOCK * 1000),
        ),
    )


def operation_id() -> str:
    return "act-" + str(uuid.uuid4())


def test_open_builds_referral_leverage_and_ioc_exact_bundle(tmp_path: Path) -> None:
    info = ActionInfo()
    action_builder = builder(tmp_path, info)
    built = action_builder.preview("OPEN_POSITION", {
        "leverage": 2, "margin_mode": "ISOLATED", "market": "BTC",
        "notional_usdc": "150", "side": "LONG",
    }, ACCOUNT)
    assert built.preview["limit_price"] == "60601"
    assert built.preview["size_asset"] == "0.00247"
    assert [item[0] for item in built.phase_specs] == [
        PhaseType.SET_REFERRER, PhaseType.SET_ISOLATED_LEVERAGE,
        PhaseType.PLACE_IOC_ORDER,
    ]
    assert [call["type"] for call in info.calls].count("referral") == 1
    assert all(call["type"] not in {"userFills", "userFillsByTime"} for call in info.calls)

    bundle = action_builder.bundle(operation_id(), built)
    bundle.validate_digest()
    parsed = ProtectedActionBundle.from_mapping(bundle.to_mapping())
    assert parsed == bundle
    assert [int(item.nonce) for item in bundle.phases] == list(
        range(int(bundle.phases[0].nonce), int(bundle.phases[0].nonce) + 3),
    )
    wire = phase_action(bundle.phases[-1])
    assert wire["orders"][0]["r"] is False
    assert wire["orders"][0]["t"] == {"limit": {"tif": "Ioc"}}
    leverage = phase_action(bundle.phases[-2])
    assert leverage == {"type": "updateLeverage", "asset": 0, "isCross": False, "leverage": 2}

    wrong_type = bundle.to_mapping()
    wrong_type["phases"][0]["nonce"] = int(bundle.phases[0].nonce)
    with pytest.raises(ContractError):
        ProtectedActionBundle.from_mapping(wrong_type)


def test_price_rounding_keeps_five_significant_figures_without_holon_notional_cap(
    tmp_path: Path,
) -> None:
    info = ActionInfo(referred_by={"code": "EXISTING"})
    info.prices["BTC"] = ("123455", "123456")
    built = builder(tmp_path, info).preview("OPEN_POSITION", {
        "leverage": 2, "margin_mode": "ISOLATED", "market": "BTC",
        "notional_usdc": "150", "side": "LONG",
    }, ACCOUNT)
    assert built.preview["limit_price"] == "124690"
    assert Decimal(built.preview["size_asset"]) * Decimal(
        built.preview["limit_price"]
    ) <= Decimal("150")


def test_existing_referrer_is_preserved_and_open_never_assigns_it(tmp_path: Path) -> None:
    info = ActionInfo(referred_by={"code": "SOMEONE"})
    built = builder(tmp_path, info).preview("OPEN_POSITION", {
        "leverage": 1, "margin_mode": "ISOLATED", "market": "ETH",
        "notional_usdc": "10", "side": "SHORT",
    }, ACCOUNT)
    assert built.referral_assignment is False
    assert PhaseType.SET_REFERRER not in [item[0] for item in built.phase_specs]


def test_cross_uses_live_market_leverage_and_exact_wire_flag(tmp_path: Path) -> None:
    info = ActionInfo(referred_by={"code": "EXISTING"})
    built = builder(tmp_path, info).preview("OPEN_POSITION", {
        "leverage": 40, "margin_mode": "CROSS", "market": "BTC",
        "notional_usdc": "100", "side": "SHORT",
    }, ACCOUNT)
    assert built.preview["margin_mode"] == "CROSS"
    assert "CROSS_MARGIN_RISK" in built.caveats
    bundle = builder(tmp_path, info).bundle(operation_id(), built)
    assert phase_action(bundle.phases[0]) == {
        "type": "updateLeverage", "asset": 0, "isCross": True, "leverage": 40,
    }

    with pytest.raises(AdapterError) as error:
        builder(tmp_path, info).preview("OPEN_POSITION", {
            "leverage": 41, "margin_mode": "CROSS", "market": "BTC",
            "notional_usdc": "100", "side": "SHORT",
        }, ACCOUNT)
    assert error.value.code == "PERPDEX_LEVERAGE_UNAVAILABLE"


def test_live_leverage_change_invalidates_the_exact_bundle(tmp_path: Path) -> None:
    info = ActionInfo(referred_by={"code": "EXISTING"})
    action_builder = builder(tmp_path, info)
    bundle = action_builder.bundle(operation_id(), action_builder.preview(
        "OPEN_POSITION", {
            "leverage": 40, "margin_mode": "ISOLATED", "market": "BTC",
            "notional_usdc": "100", "side": "LONG",
        }, ACCOUNT,
    ))
    info.max_leverages["BTC"] = 20
    with pytest.raises(AdapterError) as error:
        action_builder.verify(bundle.to_mapping(), ACCOUNT)
    assert error.value.code == "PERPDEX_LEVERAGE_UNAVAILABLE"


@pytest.mark.parametrize(
    ("info", "code"),
    [
        (ActionInfo(position="0.001"), "PERPDEX_POSITION_NOT_FLAT"),
        (ActionInfo(orders=(42,)), "PERPDEX_OPEN_ORDERS_EXIST"),
        (ActionInfo(withdrawable="1"), "PERPDEX_COLLATERAL_INSUFFICIENT"),
    ],
)
def test_open_live_preconditions_fail_closed(tmp_path: Path, info: ActionInfo, code: str) -> None:
    with pytest.raises(AdapterError) as error:
        builder(tmp_path, info).preview("OPEN_POSITION", {
            "leverage": 2, "margin_mode": "ISOLATED", "market": "BTC",
            "notional_usdc": "100", "side": "LONG",
        }, ACCOUNT)
    assert error.value.code == code


def test_close_cancels_orders_then_uses_fixed_reduce_only_ioc(tmp_path: Path) -> None:
    info = ActionInfo(position="0.002", orders=(42, 43), referred_by=None)
    action_builder = builder(tmp_path, info)
    built = action_builder.preview("CLOSE_POSITION", {
        "amount_mode": "PERCENT", "market": "BTC", "percent": "25",
    }, ACCOUNT)
    assert built.preview["close_size_asset"] == "0.0005"
    assert built.preview["cancel_order_ids"] == ["42", "43"]
    bundle = action_builder.bundle(operation_id(), built)
    assert [phase.phase_type for phase in bundle.phases] == [
        PhaseType.CANCEL_MARKET_ORDERS, PhaseType.PLACE_IOC_ORDER,
    ]
    assert phase_action(bundle.phases[0])["cancels"] == [{"a": 0, "o": 42}, {"a": 0, "o": 43}]
    order = phase_action(bundle.phases[1])["orders"][0]
    assert order["b"] is False and order["r"] is True
    assert all(call["type"] != "referral" for call in info.calls)


def test_deposit_referral_and_withdrawal_are_independent(tmp_path: Path) -> None:
    info = ActionInfo(referred_by=None, withdrawable="25", hlp_equity="20")
    action_builder = builder(tmp_path, info)
    deposit = action_builder.preview("HLP_DEPOSIT", {"amount_usdc": "25"}, ACCOUNT)
    assert [item[0] for item in deposit.phase_specs] == [
        PhaseType.SET_REFERRER, PhaseType.VAULT_TRANSFER,
    ]
    assert deposit.preview["deposit_resets_lock"] is True

    before = len(info.calls)
    withdrawal = action_builder.preview(
        "HLP_WITHDRAW", {"amount_mode": "ALL", "amount_usdc": None}, ACCOUNT,
    )
    assert withdrawal.preview["amount_usdc"] == "20"
    assert withdrawal.referral_assignment is False
    assert all(call["type"] != "referral" for call in info.calls[before:])


def test_hlp_deposit_is_limited_only_by_live_withdrawable_balance(tmp_path: Path) -> None:
    info = ActionInfo(referred_by={"code": "EXISTING"}, withdrawable="200")
    deposit = builder(tmp_path, info).preview(
        "HLP_DEPOSIT", {"amount_usdc": "150"}, ACCOUNT,
    )
    assert deposit.preview["amount_usdc"] == "150"
    with pytest.raises(AdapterError) as error:
        builder(tmp_path, info).preview(
            "HLP_DEPOSIT", {"amount_usdc": "200.000001"}, ACCOUNT,
        )
    assert error.value.code == "HLP_BALANCE_INSUFFICIENT"


def test_wallet_verify_rejects_tamper_price_move_and_position_change(tmp_path: Path) -> None:
    info = ActionInfo(position="0.002", orders=(42,))
    action_builder = builder(tmp_path, info)
    built = action_builder.preview("CLOSE_POSITION", {
        "amount_mode": "FULL", "market": "BTC", "percent": None,
    }, ACCOUNT)
    bundle = action_builder.bundle(operation_id(), built)
    assert action_builder.verify(bundle.to_mapping(), ACCOUNT) == bundle

    tampered = bundle.to_mapping()
    tampered["phases"][1]["semantic"]["reduce_only"] = False
    with pytest.raises(AdapterError) as error:
        action_builder.verify(tampered, ACCOUNT)
    assert error.value.code == "PERPDEX_BUNDLE_INVALID"

    info.prices["BTC"] = ("61000", "62000")
    with pytest.raises(AdapterError) as error:
        action_builder.verify(bundle.to_mapping(), ACCOUNT)
    assert error.value.code == "PERPDEX_PRICE_MOVED"

    info.prices["BTC"] = ("59999", "60001")
    info.position = "0.001"
    with pytest.raises(AdapterError) as error:
        action_builder.verify(bundle.to_mapping(), ACCOUNT)
    assert error.value.code == "PERPDEX_POSITION_CHANGED"


def test_guard_preview_is_single_use_and_operation_state_is_secret_free(tmp_path: Path) -> None:
    info = ActionInfo(referred_by={"code": "EXISTING"})
    adapter = GuardProtectedActionAdapter(HyperliquidReader(info), clock=lambda: CLOCK)
    adapter.configure(tmp_path)
    params = {"amount_mode": "ALL", "amount_usdc": None}
    preview = adapter.preview("HLP_WITHDRAW", params, ACCOUNT)
    bundle = adapter.prepare(
        operation_id(), "HLP_WITHDRAW", params, ACCOUNT,
        str(preview.preview_digest),
    )
    with pytest.raises(AdapterError) as error:
        adapter.prepare(
            operation_id(), "HLP_WITHDRAW", params, ACCOUNT,
            str(preview.preview_digest),
        )
    assert error.value.code == "PERPDEX_PREVIEW_EXPIRED"
    state = adapter.status(bundle.operation_id)
    assert state is not None and state["state"] == "PREPARED"
    raw = (tmp_path / "perpdex-operations.json").read_text(encoding="utf-8")
    assert all(token not in raw.casefold() for token in ("password", "private_key", "signature", "signed_payload"))


def test_order_and_hlp_reconciliation_require_matching_public_evidence(
    tmp_path: Path,
) -> None:
    from holon_perpdex.wallet import WalletProtectedActionAdapter

    info = ActionInfo(position="0.002", referred_by={"code": "EXISTING"})
    action_builder = builder(tmp_path, info)
    close = action_builder.bundle(operation_id(), action_builder.preview(
        "CLOSE_POSITION", {
            "amount_mode": "FULL", "market": "BTC", "percent": None,
        }, ACCOUNT,
    ))
    order_phase = close.phases[-1]
    requested = Decimal(order_phase.semantic["size_asset"])
    partial = requested / 2
    info.order_status = {
        "status": "order",
        "order": {
            "status": "canceled",
            "order": {"oid": 77, "sz": "0", "cloid": order_phase.cloid},
        },
    }
    info.fills = [{
        "coin": "BTC", "oid": 77, "sz": str(partial),
        "time": int(order_phase.nonce) + 1,
    }]
    wallet = WalletProtectedActionAdapter(HyperliquidReader(info), clock=lambda: CLOCK)
    order_result = wallet.reconcile(
        order_phase,
        {"status": "ok", "response": {"type": "order", "data": {"statuses": [{}]}}},
        ACCOUNT,
    )
    assert order_result["state"] == "PARTIAL"

    deposit = action_builder.bundle(operation_id(), action_builder.preview(
        "HLP_DEPOSIT", {"amount_usdc": "25"}, ACCOUNT,
    ))
    vault_phase = deposit.phases[-1]
    info.ledger_updates = [{
        "time": int(vault_phase.nonce) + 1,
        "hash": "0x" + "a" * 64,
        "delta": {
            "type": "vaultDeposit", "vault": HLP_ADDRESS, "usdc": "25",
        },
    }]
    confirmed = wallet.reconcile(
        vault_phase, {"status": "ok", "response": {"type": "default"}}, ACCOUNT,
    )
    assert confirmed == {
        "state": "CONFIRMED", "code": "HLP_DEPOSIT_CONFIRMED",
        "public_id": "0x" + "a" * 64,
    }
    info.ledger_updates[0]["delta"] = {
        "type": "vaultDeposit", "vault": HLP_ADDRESS, "usdc": "24",
    }
    uncertain = wallet.reconcile(
        vault_phase, {"status": "ok", "response": {"type": "default"}}, ACCOUNT,
    )
    assert uncertain["state"] == "UNKNOWN"


def test_nonce_and_operation_files_are_strict_and_atomic(tmp_path: Path) -> None:
    nonce_path = tmp_path / "perpdex-nonces.json"
    store = PerpDexNonceStore(nonce_path, clock_ms=lambda: 1000)
    assert store.allocate(2) == ("1000", "1001")
    assert store.allocate(1) == ("1002",)
    assert json.loads(nonce_path.read_text(encoding="utf-8"))["last_nonce"] == "1002"
    nonce_path.write_text('{"last_nonce":1,"nonce_version":"1"}\n', encoding="utf-8")
    with pytest.raises(PersistenceError):
        store.allocate(1)

    operations = PerpDexOperationStore(tmp_path / "perpdex-operations.json")
    assert operations.status(operation_id()) is None


def test_interrupted_submitting_phase_becomes_unknown_without_resubmission(
    tmp_path: Path,
) -> None:
    now = [CLOCK]
    action_builder = builder(tmp_path, ActionInfo(referred_by={"code": "EXISTING"}))
    bundle = action_builder.bundle(operation_id(), action_builder.preview(
        "OPEN_POSITION", {
            "leverage": 2, "margin_mode": "ISOLATED", "market": "BTC",
            "notional_usdc": "10", "side": "LONG",
        }, ACCOUNT,
    ))
    store = PerpDexOperationStore(
        tmp_path / "operations.json", clock=lambda: now[0],
    )
    store.begin(bundle)
    store.mark_operation(bundle.operation_id, "AWAITING_LOCAL_CONFIRMATION")
    store.mark_operation(bundle.operation_id, "EXECUTING")
    store.mark_phase(
        bundle.operation_id, bundle.phases[0].phase_id, "SUBMITTING",
        code="SUBMITTING",
    )
    now[0] += 302
    assert store.contain_stale() == 1
    contained = store.status(bundle.operation_id)
    assert contained["state"] == "UNKNOWN"
    assert contained["phases"][0]["state"] == "UNKNOWN"
