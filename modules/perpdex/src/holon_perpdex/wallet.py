"""Wallet-side factories for the bounded PerpDEX module surface."""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from pathlib import Path
import time

from .actions import AdapterError, HyperliquidActionBuilder, phase_action, phase_digest
from .contracts import PhaseType, ProtectedActionBundle, ProtectedActionPhase
from .persistence import PerpDexOperationStore
from .reader import HyperliquidReader


class WalletProtectedActionAdapter:
    """Independent Wallet-side live verifier and operation journal facade."""

    adapter_version = "1"
    profile_id = "hyperliquid-mainnet-v1"

    def __init__(self, reader: HyperliquidReader | None = None, *, clock=None) -> None:
        self.reader = reader or HyperliquidReader()
        self.clock = clock or time.time
        self._builder = HyperliquidActionBuilder(self.reader, clock=self.clock)
        self._operations: PerpDexOperationStore | None = None

    def configure(self, data_dir: Path) -> None:
        self._operations = PerpDexOperationStore(
            Path(data_dir) / "perpdex-operations.json", clock=self.clock,
        )
        self._operations.contain_stale()
        self._operations.prune_transient()

    def verify(
        self, bundle: Mapping[str, object], account: Mapping[str, str],
    ) -> ProtectedActionBundle:
        return self._builder.verify(bundle, account)

    def verify_phase(
        self, bundle: ProtectedActionBundle, phase_index: int,
        account: Mapping[str, str],
    ) -> None:
        if type(phase_index) is not int or not 0 <= phase_index < len(bundle.phases):
            raise AdapterError("PERPDEX_PHASE_INVALID", "Protected phase is invalid")
        if phase_index == 0:
            self._builder.verify(bundle.to_mapping(), account)
            return
        phase = bundle.phases[phase_index]
        current = self._builder.preview(
            bundle.intent.action_type.value, bundle.intent.to_mapping(), account,
        )
        if phase.phase_type is PhaseType.PLACE_IOC_ORDER:
            current_order = next(
                (item[1] for item in current.phase_specs if item[0] is PhaseType.PLACE_IOC_ORDER),
                None,
            )
            if current_order is None:
                raise AdapterError("PERPDEX_LIVE_STATE_CHANGED", "Order state changed")
            old = phase.semantic
            if (
                old["asset_index"] != current_order["asset_index"]
                or old["market"] != current_order["market"]
                or old["is_buy"] != current_order["is_buy"]
                or old["reduce_only"] != current_order["reduce_only"]
            ):
                raise AdapterError("PERPDEX_LIVE_STATE_CHANGED", "Position or metadata changed")
            frozen = Decimal(str(old["limit_price"]))
            reference = Decimal(str(current_order["reference_price"]))
            safe = (
                reference <= frozen <= reference * Decimal("1.01")
                if old["is_buy"]
                else reference * Decimal("0.99") <= frozen <= reference
            )
            if not safe:
                raise AdapterError("PERPDEX_PRICE_MOVED", "Frozen IOC limit is no longer safe")
            if old["reduce_only"] and old["size_asset"] != current_order["size_asset"]:
                raise AdapterError("PERPDEX_POSITION_CHANGED", "Position changed before signing")
        elif phase.phase_type is PhaseType.VAULT_TRANSFER:
            current_transfer = next(
                (item[1] for item in current.phase_specs if item[0] is PhaseType.VAULT_TRANSFER),
                None,
            )
            if current_transfer is None:
                raise AdapterError("HLP_LIVE_STATE_CHANGED", "HLP state changed")
            if Decimal(str(phase.semantic["amount_usdc"])) > Decimal(
                str(current_transfer["available_before_usdc"])
            ):
                raise AdapterError("HLP_BALANCE_CHANGED", "HLP available balance decreased")
        elif phase.phase_type is PhaseType.SET_ISOLATED_LEVERAGE:
            current_leverage = next(
                (item[1] for item in current.phase_specs if item[0] is PhaseType.SET_ISOLATED_LEVERAGE),
                None,
            )
            if current_leverage is None or dict(phase.semantic) != current_leverage:
                raise AdapterError("PERPDEX_LIVE_STATE_CHANGED", "Leverage state changed")
        elif phase.phase_type is PhaseType.CANCEL_MARKET_ORDERS:
            current_cancel = next(
                (item[1] for item in current.phase_specs if item[0] is PhaseType.CANCEL_MARKET_ORDERS),
                None,
            )
            if current_cancel is None or dict(phase.semantic) != current_cancel:
                raise AdapterError("PERPDEX_ORDERS_CHANGED", "Open orders changed")
        elif phase.phase_type is PhaseType.SET_REFERRER:
            if not current.referral_assignment:
                raise AdapterError("PERPDEX_REFERRAL_CHANGED", "Referral state changed")
        if phase_digest(phase) != phase.wire_digest:
            raise AdapterError("PERPDEX_WIRE_DIGEST_MISMATCH", "Protected action digest mismatch")

    @staticmethod
    def _ok_response(response: object) -> Mapping[str, object]:
        if not isinstance(response, Mapping):
            raise AdapterError("HYPERLIQUID_RESPONSE_INVALID", "Invalid Hyperliquid response")
        if response.get("status") == "err":
            raise AdapterError("HYPERLIQUID_ACTION_REJECTED", "Hyperliquid rejected the action")
        if response.get("status") != "ok":
            raise AdapterError("HYPERLIQUID_RESPONSE_INVALID", "Invalid Hyperliquid response")
        return response

    def reconcile(
        self, phase: ProtectedActionPhase, response: object,
        account: Mapping[str, str],
    ) -> Mapping[str, object]:
        checked = self._ok_response(response)
        if phase.phase_type is PhaseType.SET_REFERRER:
            state = self.reader.referral(account)
            return {
                "state": "CONFIRMED" if state["has_referrer"] else "UNKNOWN",
                "code": "REFERRAL_ASSIGNED" if state["has_referrer"] else "REFERRAL_RESULT_UNKNOWN",
                "public_id": None,
            }
        if phase.phase_type is PhaseType.SET_ISOLATED_LEVERAGE:
            return {"state": "CONFIRMED", "code": "ISOLATED_LEVERAGE_SET", "public_id": None}
        if phase.phase_type is PhaseType.CANCEL_MARKET_ORDERS:
            portfolio = self.reader.portfolio(account)
            remaining = {
                str(item["oid"]) for item in portfolio["orders"]
                if item["market"] == phase.semantic["market"]
            }
            expected = set(phase.semantic["order_ids"])
            confirmed = not (remaining & expected)
            return {
                "state": "CONFIRMED" if confirmed else "UNKNOWN",
                "code": "MARKET_ORDERS_CANCELLED" if confirmed else "CANCEL_RESULT_UNKNOWN",
                "public_id": ",".join(sorted(expected))[:256],
            }
        if phase.phase_type is PhaseType.PLACE_IOC_ORDER:
            result = self._order_result(phase, checked, account)
            return result
        return self._vault_result(phase, checked, account)

    def _order_result(
        self, phase: ProtectedActionPhase, response: Mapping[str, object],
        account: Mapping[str, str],
    ) -> Mapping[str, object]:
        response_value = response.get("response")
        statuses = None
        if isinstance(response_value, Mapping):
            data = response_value.get("data")
            if isinstance(data, Mapping) and isinstance(data.get("statuses"), list):
                statuses = data["statuses"]
        status = statuses[0] if statuses and len(statuses) == 1 else None
        if isinstance(status, Mapping) and isinstance(status.get("error"), str):
            return {
                "state": "FAILED", "code": "IOC_ORDER_REJECTED",
                "public_id": phase.cloid,
            }
        filled = status.get("filled") if isinstance(status, Mapping) else None
        filled_size: Decimal | None = None
        public_id: str | None = phase.cloid
        oid: str | None = None
        if isinstance(filled, Mapping):
            try:
                filled_size = Decimal(str(filled["totalSz"]))
            except Exception:
                filled_size = None
            response_oid = filled.get("oid")
            if type(response_oid) is int or isinstance(response_oid, str):
                oid = str(response_oid)
                public_id = oid
        order_state: str | None = None
        order_is_open = False
        try:
            order_status = self.reader._post({
                "type": "orderStatus", "user": account["address"], "oid": phase.cloid,
            })
            if isinstance(order_status, Mapping) and order_status.get("status") == "order":
                order_wrapper = order_status.get("order")
                if isinstance(order_wrapper, Mapping):
                    candidate_state = order_wrapper.get("status")
                    order_state = candidate_state if isinstance(candidate_state, str) else None
                    details = order_wrapper.get("order")
                    if isinstance(details, Mapping):
                        candidate_oid = details.get("oid")
                        if type(candidate_oid) is int or isinstance(candidate_oid, str):
                            oid = str(candidate_oid)
                            public_id = oid
                        if filled_size is None and order_state == "filled":
                            try:
                                filled_size = Decimal(str(details["sz"]))
                            except Exception:
                                filled_size = None
            fills = self.reader._post({
                "type": "userFillsByTime", "user": account["address"],
                "startTime": max(0, int(phase.nonce) - 60_000),
            })
            if isinstance(fills, list) and oid is not None:
                matched = Decimal(0)
                for item in fills:
                    if (
                        not isinstance(item, Mapping)
                        or str(item.get("oid")) != oid
                        or item.get("coin") != phase.semantic["market"]
                        or type(item.get("time")) is not int
                        or item["time"] < int(phase.nonce)
                    ):
                        continue
                    try:
                        matched += Decimal(str(item["sz"]))
                    except Exception:
                        continue
                if matched > 0:
                    filled_size = matched
            portfolio = self.reader.portfolio(account)
            order_is_open = any(
                item.get("cloid") == phase.cloid
                or oid is not None and str(item.get("oid")) == oid
                for item in portfolio["orders"]
            )
        except Exception:
            if filled_size is None:
                return {"state": "UNKNOWN", "code": "IOC_RESULT_UNKNOWN", "public_id": public_id}
        if order_is_open:
            return {"state": "UNKNOWN", "code": "IOC_ORDER_STILL_OPEN", "public_id": public_id}
        if filled_size is None or filled_size <= 0:
            rejected = (
                isinstance(order_state, str)
                and (
                    order_state.endswith("Rejected")
                    or order_state in {
                        "rejected", "canceled", "marginCanceled",
                        "reduceOnlyCanceled", "selfTradeCanceled",
                    }
                )
            )
            return {
                "state": "FAILED" if rejected else "UNKNOWN",
                "code": "IOC_NOT_FILLED" if rejected else "IOC_RESULT_UNKNOWN",
                "public_id": public_id,
            }
        requested = Decimal(str(phase.semantic["size_asset"]))
        if filled_size > requested:
            return {"state": "UNKNOWN", "code": "IOC_OVERFILL_UNCERTAIN", "public_id": public_id}
        if filled_size < requested:
            return {"state": "PARTIAL", "code": "IOC_PARTIAL_FILL", "public_id": public_id}
        return {"state": "CONFIRMED", "code": "IOC_FILLED", "public_id": public_id}

    def _vault_result(
        self, phase: ProtectedActionPhase, response: Mapping[str, object],
        account: Mapping[str, str],
    ) -> Mapping[str, object]:
        del response
        amount = Decimal(str(phase.semantic["amount_usdc"]))
        ledger_match = False
        public_id: str | None = None
        try:
            ledger = self.reader._post({
                "type": "userNonFundingLedgerUpdates", "user": account["address"],
                "startTime": max(0, int(phase.nonce) - 60_000),
            })
            if isinstance(ledger, list):
                for item in ledger:
                    if (
                        not isinstance(item, Mapping)
                        or type(item.get("time")) is not int
                        or item["time"] < int(phase.nonce)
                    ):
                        continue
                    delta = item.get("delta")
                    if not isinstance(delta, Mapping):
                        continue
                    expected_type = (
                        "vaultDeposit" if phase.semantic["is_deposit"]
                        else "vaultWithdraw"
                    )
                    amount_field = "usdc" if phase.semantic["is_deposit"] else "requestedUsd"
                    try:
                        ledger_amount = Decimal(str(delta[amount_field]))
                    except Exception:
                        continue
                    if (
                        str(delta.get("vault", "")).lower()
                        == phase.semantic["vault_address"]
                        and delta.get("type") == expected_type
                        and ledger_amount == amount
                        and (
                            phase.semantic["is_deposit"]
                            or not isinstance(delta.get("user"), str)
                            or delta["user"].lower() == account["address"].lower()
                        )
                    ):
                        ledger_match = True
                        candidate = item.get("hash")
                        if (
                            isinstance(candidate, str)
                            and len(candidate) == 66 and candidate.startswith("0x")
                            and all(ch in "0123456789abcdef" for ch in candidate[2:])
                        ):
                            public_id = candidate
                        break
            # Re-check the official HLP identity and current follower state;
            # equity alone is never used to confirm a transfer because PnL can
            # move independently of this operation.
            self.reader.hlp(account)
        except Exception:
            return {"state": "UNKNOWN", "code": "HLP_TRANSFER_RESULT_UNKNOWN", "public_id": None}
        confirmed = ledger_match
        return {
            "state": "CONFIRMED" if confirmed else "UNKNOWN",
            "code": "HLP_DEPOSIT_CONFIRMED" if confirmed and phase.semantic["is_deposit"] else (
                "HLP_WITHDRAW_CONFIRMED" if confirmed else "HLP_TRANSFER_RESULT_UNKNOWN"
            ),
            "public_id": public_id,
        }

    def mark_operation(self, operation_id: str, state: str) -> Mapping[str, object]:
        if self._operations is None:
            raise RuntimeError("PerpDEX operation state is unavailable")
        return self._operations.mark_operation(operation_id, state)

    def mark_external_submission_started(self, operation_id: str) -> Mapping[str, object]:
        if self._operations is None:
            raise RuntimeError("PerpDEX operation state is unavailable")
        return self._operations.mark_external_submission_started(operation_id)

    def discard_pre_submit_cancelled(self, operation_id: str) -> bool:
        if self._operations is None:
            raise RuntimeError("PerpDEX operation state is unavailable")
        return self._operations.discard_pre_submit_cancelled(operation_id)

    @staticmethod
    def wire_action(phase: ProtectedActionPhase) -> Mapping[str, object]:
        return phase_action(phase)

    @staticmethod
    def wire_digest(phase: ProtectedActionPhase) -> str:
        return phase_digest(phase)

    def mark_phase(
        self, operation_id: str, phase_id: str, state: str, *,
        code: str | None = None, public_id: str | None = None,
    ) -> Mapping[str, object]:
        if self._operations is None:
            raise RuntimeError("PerpDEX operation state is unavailable")
        return self._operations.mark_phase(
            operation_id, phase_id, state, code=code, public_id=public_id,
        )

    def status(self, operation_id: str) -> Mapping[str, object] | None:
        return None if self._operations is None else self._operations.status(operation_id)

    def history(
        self, account: Mapping[str, str], *, limit: int = 20,
    ) -> tuple[dict[str, object], ...]:
        """Return a bounded presentation view without nonces or wire digests."""
        if (
            self._operations is None or type(limit) is not int
            or not 1 <= limit <= 20
            or not isinstance(account, Mapping)
            or not isinstance(account.get("address"), str)
        ):
            return ()
        values = self._operations.latest(str(account["address"]))[:limit]
        return tuple({
            "action_type": item["action_type"],
            "created_at": item["created_at"],
            "intent": dict(item["intent"]),
            "operation_id": item["operation_id"],
            "phases": [{
                "code": phase["code"],
                "phase_type": phase["phase_type"],
                "public_id": phase["public_id"],
                "state": phase["state"],
            } for phase in item["phases"]],
            "state": item["state"],
            "updated_at": item["updated_at"],
        } for item in values)


def create_protected_adapter() -> WalletProtectedActionAdapter:
    return WalletProtectedActionAdapter()


def create_earn_provider():
    from .earn import HlpEarnProvider
    from .reader import HyperliquidReader

    return HlpEarnProvider(HyperliquidReader())


def create_reader() -> HyperliquidReader:
    return HyperliquidReader()


def create_page_model() -> dict[str, str]:
    return {
        "actionCapabilityId": "holon.perpdex.action.guard",
        "body": "Hyperliquid public data and protected actions.",
        "moduleId": "holon.perpdex",
        "readCapabilityId": "holon.perpdex.read.wallet",
        "title": "PerpDEX",
    }
