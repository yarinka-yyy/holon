"""Closed Hyperliquid V1 action builder and independent live verifier."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_CEILING, ROUND_DOWN, ROUND_FLOOR
from collections.abc import Mapping
import hashlib
import time

from .contracts import (
    ActionType, AmountMode, ContractError, PerpDexActionIntent,
    MarginMode, PerpDexActionPreview, PhaseType, PositionSide, ProtectedActionBundle,
    ProtectedActionPhase, digest_json,
)
from .persistence import PerpDexNonceStore
from .profile import (
    HLP_ADDRESS, HLP_LOCKUP_DAYS, HLP_NAME, MAX_SLIPPAGE_PERCENT,
    PROFILE_DIGEST, REFERRAL_CODE, REFERRAL_DISCLOSURE,
)
from .reader import HyperliquidReader, ReaderError

BOOK_MAX_AGE_MS = 15_000
BOOK_FUTURE_TOLERANCE_MS = 5_000


class AdapterError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class BuiltPreview:
    intent: PerpDexActionIntent
    account: dict[str, str]
    preview: dict[str, object]
    snapshot_digest: str
    checks: tuple[str, ...]
    caveats: tuple[str, ...]
    referral_assignment: bool
    phase_specs: tuple[tuple[PhaseType, dict[str, object], str | None], ...]


def _text(value: Decimal) -> str:
    text = format(value, "f")
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text or "0"


def _decimal(value: object, label: str) -> Decimal:
    try:
        parsed = Decimal(str(value))
    except Exception as exc:
        raise AdapterError("HYPERLIQUID_DATA_INVALID", f"Invalid {label}") from exc
    if not parsed.is_finite():
        raise AdapterError("HYPERLIQUID_DATA_INVALID", f"Invalid {label}")
    return parsed


def _timestamp(value: float) -> str:
    return datetime.fromtimestamp(value, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _expires_after_ms(value: str) -> int:
    parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    return int(parsed.timestamp() * 1000)


def _round_size(value: Decimal, sz_decimals: int) -> Decimal:
    quantum = Decimal(1).scaleb(-sz_decimals)
    return value.quantize(quantum, rounding=ROUND_DOWN)


def _round_price(value: Decimal, sz_decimals: int, *, buy: bool) -> Decimal:
    # Hyperliquid perps allow at most five significant figures and at most
    # ``6 - szDecimals`` decimal places. Use directional rounding so the
    # resulting IOC price never crosses the frozen one-percent boundary.
    significant_exponent = value.adjusted() - 4
    decimal_exponent = sz_decimals - 6
    quantum = Decimal(1).scaleb(max(significant_exponent, decimal_exponent))
    return value.quantize(quantum, rounding=ROUND_FLOOR if buy else ROUND_CEILING)


def _phase_id(operation_id: str, index: int, phase_type: PhaseType) -> str:
    value = hashlib.sha256(f"{operation_id}:{index}:{phase_type.value}".encode()).hexdigest()
    return "phase-" + value[:32]


def _cloid(operation_id: str, index: int) -> str:
    return "0x" + hashlib.sha256(f"{operation_id}:{index}:IOC".encode()).hexdigest()[:32]


def phase_action(phase: ProtectedActionPhase) -> dict[str, object]:
    """Translate only a validated closed phase into the SDK wire action shape."""
    semantic = phase.semantic
    if phase.phase_type is PhaseType.SET_REFERRER:
        return {"type": "setReferrer", "code": semantic["code"]}
    if phase.phase_type is PhaseType.SET_ISOLATED_LEVERAGE:
        return {
            "type": "updateLeverage", "asset": semantic["asset_index"],
            "isCross": semantic["is_cross"], "leverage": semantic["leverage"],
        }
    if phase.phase_type is PhaseType.CANCEL_MARKET_ORDERS:
        return {
            "type": "cancel",
            "cancels": [
                {"a": semantic["asset_index"], "o": int(order_id)}
                for order_id in semantic["order_ids"]
            ],
        }
    if phase.phase_type is PhaseType.PLACE_IOC_ORDER:
        return {
            "type": "order",
            "orders": [{
                "a": semantic["asset_index"], "b": semantic["is_buy"],
                "p": semantic["limit_price"], "s": semantic["size_asset"],
                "r": semantic["reduce_only"], "t": {"limit": {"tif": "Ioc"}},
                "c": phase.cloid,
            }],
            "grouping": "na",
        }
    return {
        "type": "vaultTransfer", "vaultAddress": semantic["vault_address"],
        "isDeposit": semantic["is_deposit"], "usd": int(semantic["usd_atomic"]),
    }


def phase_digest(phase: ProtectedActionPhase) -> str:
    try:
        from hyperliquid.utils.signing import action_hash
    except ImportError as exc:
        raise AdapterError("HYPERLIQUID_SDK_UNAVAILABLE", "Hyperliquid action support is unavailable") from exc
    return action_hash(
        phase_action(phase), None, int(phase.nonce), _expires_after_ms(phase.expires_at),
    ).hex()


class HyperliquidActionBuilder:
    def __init__(
        self, reader: HyperliquidReader | None = None, *, clock=None,
        nonce_store: PerpDexNonceStore | None = None,
    ) -> None:
        self.reader = reader or HyperliquidReader()
        self.clock = clock or time.time
        self.nonce_store = nonce_store

    def preview(
        self, action_type: object, params: Mapping[str, object],
        account: Mapping[str, str],
    ) -> BuiltPreview:
        intent = PerpDexActionIntent.from_mapping(action_type, params)
        checked_account = self._account(account)
        if intent.action_type is ActionType.OPEN_POSITION:
            return self._open(intent, checked_account)
        if intent.action_type is ActionType.CLOSE_POSITION:
            return self._close(intent, checked_account)
        if intent.action_type is ActionType.HLP_DEPOSIT:
            return self._deposit(intent, checked_account)
        return self._withdraw(intent, checked_account)

    @staticmethod
    def _account(value: Mapping[str, str]) -> dict[str, str]:
        try:
            address = str(value["address"]).lower()
            label = str(value["label"])
        except Exception as exc:
            raise AdapterError("WALLET_ACCOUNT_UNAVAILABLE", "Active Wallet account is unavailable") from exc
        if not address.startswith("0x") or len(address) != 42 or not label:
            raise AdapterError("WALLET_ACCOUNT_UNAVAILABLE", "Active Wallet account is unavailable")
        return {"address": address, "label": label}

    def _markets(self, market: str) -> dict[str, object]:
        try:
            result = self.reader.markets()
        except ReaderError as exc:
            raise AdapterError(exc.code, "Hyperliquid market data is unavailable") from exc
        selected = next((item for item in result["markets"] if item["market"] == market), None)
        if selected is None:
            raise AdapterError("PERPDEX_MARKET_UNAVAILABLE", "Selected market is unavailable")
        now_ms = int(self.clock() * 1000)
        book_time = selected["book_time_ms"]
        if (
            type(book_time) is not int
            or book_time < now_ms - BOOK_MAX_AGE_MS
            or book_time > now_ms + BOOK_FUTURE_TOLERANCE_MS
        ):
            raise AdapterError("PERPDEX_BOOK_STALE", "Hyperliquid order book is stale")
        return dict(selected)

    def _portfolio(self, account: Mapping[str, str]) -> dict[str, object]:
        try:
            return self.reader.portfolio(account)
        except ReaderError as exc:
            raise AdapterError(exc.code, "Hyperliquid portfolio is unavailable") from exc

    def _referral(self, account: Mapping[str, str]) -> bool:
        try:
            result = self.reader.referral(account)
        except ReaderError as exc:
            raise AdapterError(exc.code, "Hyperliquid referral state is unavailable") from exc
        return not bool(result["has_referrer"])

    def _hlp(self, account: Mapping[str, str]) -> dict[str, object]:
        try:
            return self.reader.hlp(account)
        except ReaderError as exc:
            raise AdapterError(exc.code, "Official HLP state is unavailable") from exc

    def _order_prices(self, market: Mapping[str, object], buy: bool) -> tuple[Decimal, Decimal]:
        reference = _decimal(market["best_ask"] if buy else market["best_bid"], "BBO")
        multiplier = Decimal("1.01") if buy else Decimal("0.99")
        limit = _round_price(
            reference * multiplier, int(market["sz_decimals"]), buy=buy,
        )
        if (buy and limit < reference) or (not buy and limit > reference):
            raise AdapterError("PERPDEX_PRICE_INVALID", "A safe IOC limit cannot be represented")
        return reference, limit

    def _common_snapshot(
        self, market: Mapping[str, object], portfolio: Mapping[str, object],
        position: Mapping[str, object] | None, orders: list[Mapping[str, object]],
    ) -> dict[str, object]:
        return {
            "asset_index": market["asset_index"], "best_ask": market["best_ask"],
            "best_bid": market["best_bid"], "book_time_ms": market["book_time_ms"],
            "market": market["market"], "orders": [dict(item) for item in orders],
            "max_exchange_leverage": market["max_exchange_leverage"],
            "position": dict(position) if position is not None else None,
            "sz_decimals": market["sz_decimals"],
            "withdrawable_usdc": portfolio["withdrawable_usdc"],
        }

    def _market_state(
        self, intent: PerpDexActionIntent, account: Mapping[str, str],
    ) -> tuple[dict[str, object], dict[str, object], Mapping[str, object] | None, list[Mapping[str, object]]]:
        assert intent.market is not None
        market = self._markets(intent.market)
        portfolio = self._portfolio(account)
        positions = [item for item in portfolio["positions"] if item["market"] == intent.market]
        orders = [item for item in portfolio["orders"] if item["market"] == intent.market]
        if len(positions) > 1:
            raise AdapterError("PERPDEX_POSITION_INVALID", "Position state is ambiguous")
        return market, portfolio, positions[0] if positions else None, orders

    def _open(self, intent: PerpDexActionIntent, account: dict[str, str]) -> BuiltPreview:
        market, portfolio, position, orders = self._market_state(intent, account)
        if position is not None:
            raise AdapterError("PERPDEX_POSITION_NOT_FLAT", "Open requires a zero position")
        if orders:
            raise AdapterError("PERPDEX_OPEN_ORDERS_EXIST", "Open requires no market orders")
        if intent.leverage is None or intent.leverage > int(market["max_exchange_leverage"]):
            raise AdapterError("PERPDEX_LEVERAGE_UNAVAILABLE", "Selected leverage is unavailable")
        buy = intent.side is PositionSide.LONG
        reference, limit = self._order_prices(market, buy)
        notional = _decimal(intent.notional_usdc, "notional")
        # A buy can execute as high as its frozen limit. Derive size from the
        # highest possible execution price so the accepted notional cap is a
        # true upper bound rather than only a quote-time estimate.
        size_price = max(reference, limit)
        size = _round_size(notional / size_price, int(market["sz_decimals"]))
        if size <= 0:
            raise AdapterError("PERPDEX_SIZE_TOO_SMALL", "Order size rounds to zero")
        try:
            fees = self.reader.fees(account)
        except ReaderError as exc:
            raise AdapterError(exc.code, "Hyperliquid fee state is unavailable") from exc
        bounded_notional = size * size_price
        required = (
            bounded_notional / Decimal(intent.leverage or 1)
            + bounded_notional * _decimal(fees["taker_rate"], "taker fee")
        )
        if required > _decimal(portfolio["withdrawable_usdc"], "withdrawable balance"):
            raise AdapterError("PERPDEX_COLLATERAL_INSUFFICIENT", "Available collateral is insufficient")
        referral = self._referral(account)
        order_semantic = {
            "asset_index": market["asset_index"], "is_buy": buy,
            "limit_price": _text(limit), "market": intent.market,
            "max_slippage_percent": MAX_SLIPPAGE_PERCENT,
            "reduce_only": False, "reference_price": _text(reference),
            "size_asset": _text(size), "position_size_before_asset": "0",
        }
        specs: list[tuple[PhaseType, dict[str, object], str | None]] = []
        if referral:
            specs.append((PhaseType.SET_REFERRER, {"code": REFERRAL_CODE}, None))
        specs.extend((
            (PhaseType.SET_ISOLATED_LEVERAGE, {
                "asset_index": market["asset_index"],
                "is_cross": intent.margin_mode is MarginMode.CROSS,
                "leverage": intent.leverage, "market": intent.market,
            }, None),
            (PhaseType.PLACE_IOC_ORDER, order_semantic, "IOC"),
        ))
        snapshot = self._common_snapshot(market, portfolio, position, orders)
        snapshot.update({"referral_assignment": referral, "taker_rate": fees["taker_rate"]})
        preview = {
            "action_type": intent.action_type.value,
            "estimated_margin_usdc": _text(notional / Decimal(intent.leverage or 1)),
            "leverage": intent.leverage, "limit_price": _text(limit),
            "margin_mode": intent.margin_mode.value if intent.margin_mode else None,
            "max_slippage_percent": MAX_SLIPPAGE_PERCENT,
            "notional_usdc": intent.notional_usdc,
            "phase_types": [item[0].value for item in specs],
            "reference_price": _text(reference), "referral_assignment": referral,
            "side": intent.side.value if intent.side else None,
            "size_asset": _text(size),
        }
        return BuiltPreview(
            intent, account, preview, digest_json(snapshot),
            ("MARKET_VERIFIED", "FLAT_POSITION_VERIFIED", "NO_OPEN_ORDERS", "COLLATERAL_VERIFIED", "REFERRAL_STATE_VERIFIED"),
            ("IOC_PARTIAL_FILL_POSSIBLE",)
            + (("CROSS_MARGIN_RISK",) if intent.margin_mode is MarginMode.CROSS else ())
            + (("REFERRAL_ASSIGNMENT_REQUIRED",) if referral else ()),
            referral, tuple(specs),
        )

    def _close(self, intent: PerpDexActionIntent, account: dict[str, str]) -> BuiltPreview:
        market, portfolio, position, orders = self._market_state(intent, account)
        if position is None:
            raise AdapterError("PERPDEX_POSITION_ABSENT", "No position is available to close")
        position_size = abs(_decimal(position["size_asset"], "position size"))
        if intent.amount_mode is AmountMode.FULL:
            size = position_size
        else:
            size = _round_size(
                position_size * _decimal(intent.percent, "close percent") / Decimal(100),
                int(market["sz_decimals"]),
            )
        if size <= 0 or size > position_size:
            raise AdapterError("PERPDEX_CLOSE_SIZE_INVALID", "Close size is invalid")
        buy = position["side"] == "SHORT"
        reference, limit = self._order_prices(market, buy)
        specs: list[tuple[PhaseType, dict[str, object], str | None]] = []
        order_ids = [str(item["oid"]) for item in orders]
        if order_ids:
            specs.append((PhaseType.CANCEL_MARKET_ORDERS, {
                "asset_index": market["asset_index"], "market": intent.market,
                "order_ids": order_ids,
            }, None))
        specs.append((PhaseType.PLACE_IOC_ORDER, {
            "asset_index": market["asset_index"], "is_buy": buy,
            "limit_price": _text(limit), "market": intent.market,
            "max_slippage_percent": MAX_SLIPPAGE_PERCENT, "reduce_only": True,
            "reference_price": _text(reference), "size_asset": _text(size),
            "position_size_before_asset": _text(position_size),
        }, "IOC"))
        snapshot = self._common_snapshot(market, portfolio, position, orders)
        preview = {
            "action_type": intent.action_type.value,
            "amount_mode": intent.amount_mode.value if intent.amount_mode else None,
            "cancel_order_ids": order_ids, "close_size_asset": _text(size),
            "current_position_size_asset": _text(position_size),
            "current_side": position["side"], "limit_price": _text(limit),
            "market": intent.market, "max_slippage_percent": MAX_SLIPPAGE_PERCENT,
            "percent": intent.percent, "phase_types": [item[0].value for item in specs],
            "reduce_only": True, "reference_price": _text(reference),
        }
        return BuiltPreview(
            intent, account, preview, digest_json(snapshot),
            ("MARKET_VERIFIED", "POSITION_VERIFIED", "REDUCE_ONLY_FIXED", "OPEN_ORDERS_ENUMERATED"),
            ("OPEN_ORDERS_WILL_BE_CANCELLED", "IOC_PARTIAL_FILL_POSSIBLE"),
            False, tuple(specs),
        )

    def _deposit(self, intent: PerpDexActionIntent, account: dict[str, str]) -> BuiltPreview:
        portfolio = self._portfolio(account)
        hlp = self._hlp(account)
        if hlp["is_closed"] or not hlp["allow_deposits"]:
            raise AdapterError("HLP_DEPOSIT_UNAVAILABLE", "Official HLP is not accepting deposits")
        amount = _decimal(intent.amount_usdc, "deposit amount")
        if amount > _decimal(portfolio["withdrawable_usdc"], "withdrawable balance"):
            raise AdapterError("HLP_BALANCE_INSUFFICIENT", "Trading account balance is insufficient")
        referral = self._referral(account)
        atomic = int(amount * Decimal(1_000_000))
        specs: list[tuple[PhaseType, dict[str, object], str | None]] = []
        if referral:
            specs.append((PhaseType.SET_REFERRER, {"code": REFERRAL_CODE}, None))
        specs.append((PhaseType.VAULT_TRANSFER, {
            "amount_usdc": _text(amount), "is_deposit": True,
            "available_before_usdc": portfolio["withdrawable_usdc"],
            "equity_before_usdc": hlp["equity_usdc"],
            "usd_atomic": str(atomic), "vault_address": HLP_ADDRESS,
        }, None))
        snapshot = {
            "allow_deposits": hlp["allow_deposits"], "equity_usdc": hlp["equity_usdc"],
            "is_closed": hlp["is_closed"], "referral_assignment": referral,
            "vault_address": hlp["vault_address"], "vault_name": hlp["vault_name"],
            "withdrawable_usdc": portfolio["withdrawable_usdc"],
        }
        preview = {
            "action_type": intent.action_type.value, "amount_usdc": _text(amount),
            "deposit_resets_lock": True, "lockup_days": HLP_LOCKUP_DAYS,
            "phase_types": [item[0].value for item in specs],
            "referral_assignment": referral, "trading_withdrawable_usdc": portfolio["withdrawable_usdc"],
            "vault_address": HLP_ADDRESS, "vault_name": HLP_NAME,
        }
        return BuiltPreview(
            intent, account, preview, digest_json(snapshot),
            ("HLP_IDENTITY_VERIFIED", "HLP_DEPOSIT_AVAILABLE", "BALANCE_VERIFIED", "REFERRAL_STATE_VERIFIED"),
            ("HLP_FOUR_DAY_LOCK", "HLP_DEPOSIT_RESETS_LOCK") + (("REFERRAL_ASSIGNMENT_REQUIRED",) if referral else ()),
            referral, tuple(specs),
        )

    def _withdraw(self, intent: PerpDexActionIntent, account: dict[str, str]) -> BuiltPreview:
        hlp = self._hlp(account)
        available = _decimal(hlp["withdrawable_equity_usdc"], "unlocked HLP equity")
        if not hlp["unlocked"] or available <= 0:
            raise AdapterError("HLP_LOCKED", "HLP equity is currently locked")
        requested = available if intent.amount_mode is AmountMode.ALL else _decimal(intent.amount_usdc, "withdrawal amount")
        atomic = int((requested * Decimal(1_000_000)).to_integral_value(rounding=ROUND_DOWN))
        amount = Decimal(atomic) / Decimal(1_000_000)
        if atomic <= 0 or requested > available or amount > available:
            raise AdapterError("HLP_EQUITY_INSUFFICIENT", "Unlocked HLP equity is insufficient")
        semantic = {
            "amount_usdc": _text(amount), "is_deposit": False,
            "available_before_usdc": hlp["withdrawable_equity_usdc"],
            "equity_before_usdc": hlp["equity_usdc"],
            "usd_atomic": str(atomic), "vault_address": HLP_ADDRESS,
        }
        snapshot = {
            "equity_usdc": hlp["equity_usdc"], "lockup_until_ms": hlp["lockup_until_ms"],
            "unlocked": hlp["unlocked"], "vault_address": hlp["vault_address"],
            "vault_name": hlp["vault_name"],
            "withdrawable_equity_usdc": hlp["withdrawable_equity_usdc"],
        }
        preview = {
            "action_type": intent.action_type.value,
            "amount_mode": intent.amount_mode.value if intent.amount_mode else None,
            "amount_usdc": _text(amount), "phase_types": [PhaseType.VAULT_TRANSFER.value],
            "referral_assignment": False, "unlocked_equity_usdc": hlp["withdrawable_equity_usdc"],
            "vault_address": HLP_ADDRESS, "vault_name": HLP_NAME,
        }
        return BuiltPreview(
            intent, account, preview, digest_json(snapshot),
            ("HLP_IDENTITY_VERIFIED", "HLP_UNLOCKED_EQUITY_VERIFIED"),
            (), False, ((PhaseType.VAULT_TRANSFER, semantic, None),),
        )

    def public_preview(self, built: BuiltPreview) -> PerpDexActionPreview:
        expires = _timestamp(self.clock() + built.intent.review_seconds)
        digest = digest_json({
            "account": built.account["address"], "action_type": built.intent.action_type.value,
            "intent": built.intent.to_mapping(), "preview": built.preview,
            "profile_digest": PROFILE_DIGEST, "snapshot_digest": built.snapshot_digest,
        })
        return PerpDexActionPreview(
            "PREVIEW_READY", built.intent.action_type, built.account, built.preview,
            digest, expires, built.checks, built.caveats,
            "MODULE_ACTION_PREVIEW_READY", "PerpDEX action preview is ready.",
        )

    def bundle(self, operation_id: str, built: BuiltPreview) -> ProtectedActionBundle:
        if self.nonce_store is None:
            raise AdapterError("PERPDEX_NONCE_STATE_UNAVAILABLE", "PerpDEX nonce state is unavailable")
        created = self.clock()
        expires_at = _timestamp(created + built.intent.review_seconds)
        nonces = self.nonce_store.allocate(len(built.phase_specs))
        phases: list[ProtectedActionPhase] = []
        for index, ((phase_type, semantic, cloid_marker), nonce) in enumerate(zip(built.phase_specs, nonces, strict=True)):
            cloid = _cloid(operation_id, index) if cloid_marker is not None else None
            provisional = ProtectedActionPhase(
                _phase_id(operation_id, index, phase_type), phase_type, nonce,
                expires_at, semantic, "0" * 64, cloid,
            )
            phases.append(replace(provisional, wire_digest=phase_digest(provisional)))
        provisional_bundle = ProtectedActionBundle(
            operation_id, built.account["address"], built.intent,
            built.snapshot_digest, _timestamp(created), expires_at, tuple(phases),
            REFERRAL_DISCLOSURE if built.referral_assignment else None, "0" * 64,
        )
        return replace(
            provisional_bundle,
            bundle_digest=digest_json(provisional_bundle.material_mapping()),
        )

    def verify(self, raw_bundle: Mapping[str, object], account: Mapping[str, str]) -> ProtectedActionBundle:
        try:
            bundle = ProtectedActionBundle.from_mapping(raw_bundle)
        except ContractError as exc:
            raise AdapterError("PERPDEX_BUNDLE_INVALID", "Protected action bundle is invalid") from exc
        checked_account = self._account(account)
        if bundle.account != checked_account["address"]:
            raise AdapterError("PERPDEX_ACCOUNT_CHANGED", "Active Wallet account changed")
        if self.clock() * 1000 >= _expires_after_ms(bundle.expires_at):
            raise AdapterError("PERPDEX_REVIEW_EXPIRED", "Protected action review expired")
        current = self.preview(bundle.intent.action_type.value, bundle.intent.to_mapping(), checked_account)
        phase_types = [phase.phase_type for phase in bundle.phases]
        current_types = [item[0] for item in current.phase_specs]
        if phase_types != current_types:
            raise AdapterError("PERPDEX_LIVE_STATE_CHANGED", "Protected action phases changed")
        for phase in bundle.phases:
            if phase.phase_type is PhaseType.PLACE_IOC_ORDER:
                current_order = next(item[1] for item in current.phase_specs if item[0] is PhaseType.PLACE_IOC_ORDER)
                old = phase.semantic
                if (
                    old["asset_index"] != current_order["asset_index"]
                    or old["market"] != current_order["market"]
                    or old["is_buy"] != current_order["is_buy"]
                    or old["reduce_only"] != current_order["reduce_only"]
                ):
                    raise AdapterError("PERPDEX_LIVE_STATE_CHANGED", "Position or metadata changed")
                frozen_limit = _decimal(old["limit_price"], "frozen limit")
                current_reference = _decimal(current_order["reference_price"], "current BBO")
                if old["is_buy"]:
                    safe = current_reference <= frozen_limit <= current_reference * Decimal("1.01")
                else:
                    safe = current_reference * Decimal("0.99") <= frozen_limit <= current_reference
                if not safe:
                    raise AdapterError("PERPDEX_PRICE_MOVED", "Frozen IOC limit is no longer safe")
                if old["reduce_only"] and old["size_asset"] != current_order["size_asset"]:
                    raise AdapterError("PERPDEX_POSITION_CHANGED", "Position changed before signing")
            elif phase.phase_type is PhaseType.CANCEL_MARKET_ORDERS:
                current_cancel = next(item[1] for item in current.phase_specs if item[0] is PhaseType.CANCEL_MARKET_ORDERS)
                if dict(phase.semantic) != current_cancel:
                    raise AdapterError("PERPDEX_ORDERS_CHANGED", "Open orders changed before signing")
            elif phase.phase_type is PhaseType.VAULT_TRANSFER:
                current_transfer = next(item[1] for item in current.phase_specs if item[0] is PhaseType.VAULT_TRANSFER)
                if phase.semantic["is_deposit"]:
                    if dict(phase.semantic) != current_transfer:
                        raise AdapterError("PERPDEX_BALANCE_CHANGED", "HLP deposit state changed")
                elif _decimal(phase.semantic["amount_usdc"], "withdraw amount") > _decimal(current.preview["unlocked_equity_usdc"], "unlocked equity"):
                    raise AdapterError("HLP_EQUITY_CHANGED", "Unlocked HLP equity decreased")
            elif dict(phase.semantic) != next(item[1] for item in current.phase_specs if item[0] is phase.phase_type):
                raise AdapterError("PERPDEX_LIVE_STATE_CHANGED", "Protected action state changed")
            if phase_digest(phase) != phase.wire_digest:
                raise AdapterError("PERPDEX_WIRE_DIGEST_MISMATCH", "Protected action digest mismatch")
        return bundle
