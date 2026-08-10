"""Guard-side live preview for the only supported Hyperliquid funding route."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import time

from holon_wallet.model import ProfileSummary
from holon_wallet.transfer import PendingTransferRequest, TransferPreflightError, TransferPreflightService

from .contracts import ActionType, ContractError, PerpDexActionIntent, PerpDexActionPreview, PhaseType, ProtectedActionPhase, digest_json
from .funding_contracts import FundingBundle
from .funding_profile import ACTION_TYPE, ARBITRUM_CHAIN_ID, ARBITRUM_NETWORK_ID, BRIDGE2_ADDRESS, FEE_BPS_DENOMINATOR, FEE_CEILING_BPS, NATIVE_USDC, REVIEW_SECONDS
from .persistence import PerpDexOperationStore


class FundingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FundingPreview:
    account: str
    intent: PerpDexActionIntent
    fee_ceiling_wei: int
    expires_at: float


class FundingGuardAdapter:
    adapter_version = "1"
    profile_id = "hyperliquid-arbitrum-funding-v1"
    wallet_capability_id = "holon.perpdex.funding.wallet"

    def __init__(self, preflight: TransferPreflightService | None = None, *, clock=None) -> None:
        self._preflight, self._clock = preflight or TransferPreflightService(), clock or time.time
        self._operations: PerpDexOperationStore | None = None
        self._previews: dict[str, FundingPreview] = {}

    def configure(self, data_dir) -> None:
        self._operations = PerpDexOperationStore(data_dir / "perpdex-operations.json", clock=self._clock)
        self._operations.contain_stale()

    def preview(self, action_type: object, params: Mapping[str, object], account: Mapping[str, str]) -> PerpDexActionPreview:
        intent, action = self._quote(action_type, params, account)
        address, ceiling = account["address"].lower(), _fee_ceiling(action.max_total_fee_wei)
        preview = {
            "amount_usdc": intent.amount_usdc, "bridge_address": BRIDGE2_ADDRESS, "chain_id": 42161,
            "max_total_fee_wei": str(ceiling), "network": "Arbitrum One", "native_usdc_address": NATIVE_USDC,
        }
        digest = digest_json({"account": address, "intent": intent.to_mapping(), "preview": preview})
        deadline = self._clock() + REVIEW_SECONDS
        self._previews = {key: value for key, value in self._previews.items() if value.expires_at > self._clock()}
        self._previews[digest] = FundingPreview(address, intent, ceiling, deadline)
        return PerpDexActionPreview(
            "PREVIEW_READY", ActionType.FUND_TRADING_ACCOUNT, {"address": address, "label": account.get("label", "")},
            preview, digest, self._timestamp(deadline),
            ("ARBITRUM_CHAIN_VERIFIED", "NATIVE_USDC_VERIFIED", "BALANCE_VERIFIED", "GAS_VERIFIED"),
            ("BRIDGE_DEPOSIT_IRREVERSIBLE", "HYPERLIQUID_CREDIT_PENDING"),
            "MODULE_ACTION_PREVIEW_READY", "Hyperliquid funding preview is ready.",
        )

    def prepare(self, operation_id: str, action_type: object, params: Mapping[str, object], account: Mapping[str, str], preview_digest: str) -> FundingBundle:
        cached = self._previews.pop(preview_digest, None)
        if cached is None or cached.expires_at <= self._clock():
            raise FundingError("FUNDING_PREVIEW_EXPIRED")
        if str(account.get("address", "")).lower() != cached.account:
            raise FundingError("FUNDING_ACCOUNT_CHANGED")
        intent, action = self._quote(action_type, params, account)
        if intent != cached.intent:
            raise FundingError("FUNDING_AMOUNT_CHANGED")
        if action.max_total_fee_wei > cached.fee_ceiling_wei:
            raise FundingError("FUNDING_GUARD_FEE_CAP_EXCEEDED")
        created, deadline = self._timestamp(self._clock()), self._timestamp(self._clock() + REVIEW_SECONDS)
        semantic = {
            "amount_usdc": intent.amount_usdc, "bridge_address": BRIDGE2_ADDRESS, "chain_id": 42161,
            "max_total_fee_wei": str(cached.fee_ceiling_wei), "token_contract": NATIVE_USDC,
            "usd_atomic": str(action.amount_atomic),
        }
        phase = ProtectedActionPhase(
            "phase-" + hashlib.sha256(operation_id.encode()).hexdigest()[:32], PhaseType.ARBITRUM_USDC_TRANSFER,
            str(action.transaction.nonce), deadline, semantic, digest_json(semantic), None,
        )
        bundle = FundingBundle(
            operation_id, account["address"].lower(), intent,
            digest_json({"block": action.block_number, "fee": str(action.max_total_fee_wei), "fee_ceiling": semantic["max_total_fee_wei"]}),
            created, deadline, (phase,), "0" * 64,
        )
        bundle = replace(bundle, bundle_digest=digest_json(bundle.material_mapping()))
        if self._operations is None:
            raise FundingError("FUNDING_OPERATION_STATE_UNAVAILABLE")
        self._operations.begin(bundle)
        return bundle

    def mark_awaiting_confirmation(self, operation_id: str) -> None:
        self._store().mark_operation(operation_id, "AWAITING_LOCAL_CONFIRMATION")

    def mark_operation(self, operation_id: str, state: str) -> None: self._store().mark_operation(operation_id, state)
    def mark_phase(self, operation_id: str, phase_id: str, state: str, **kwargs) -> None: self._store().mark_phase(operation_id, phase_id, state, **kwargs)
    def reject(self, operation_id: str) -> None: self._store().mark_operation(operation_id, "REJECTED")
    def status(self, operation_id: str): return self._store().status(operation_id)

    def _quote(self, action_type: object, params: Mapping[str, object], account: Mapping[str, str]):
        try:
            intent = PerpDexActionIntent.from_mapping(action_type, params)
            if intent.action_type is not ActionType.FUND_TRADING_ACCOUNT:
                raise ContractError("Invalid funding action")
            address, now = str(account["address"]), datetime.fromtimestamp(self._clock(), UTC)
            profile = ProfileSummary("funding-preview", str(account.get("label", "Wallet")), address, "mnemonic", "", "")
            request = PendingTransferRequest("funding-preview", profile.profile_id, now, now + timedelta(seconds=REVIEW_SECONDS), ARBITRUM_NETWORK_ID, "usdc", int(Decimal(intent.amount_usdc) * Decimal(1_000_000)))
            action = self._preflight.prepare(request, profile, BRIDGE2_ADDRESS)
            if (action.network_id != ARBITRUM_NETWORK_ID or action.chain_id != ARBITRUM_CHAIN_ID or action.asset_id != "usdc" or action.token_contract is None or action.token_contract.lower() != NATIVE_USDC.lower() or action.decimals != 6 or action.recipient.lower() != BRIDGE2_ADDRESS.lower()):
                raise FundingError("FUNDING_GUARD_ROUTE_CHANGED")
            return intent, action
        except TransferPreflightError as exc:
            raise FundingError("FUNDING_" + exc.code.value) from exc
        except (KeyError, TypeError, ValueError, ContractError) as exc:
            raise FundingError("FUNDING_INTENT_INVALID") from exc

    def _store(self) -> PerpDexOperationStore:
        if self._operations is None:
            raise FundingError("FUNDING_OPERATION_STATE_UNAVAILABLE")
        return self._operations

    @staticmethod
    def _timestamp(value: float) -> str:
        return datetime.fromtimestamp(value, UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def create_funding_protected_adapter() -> FundingGuardAdapter:
    return FundingGuardAdapter()


def _fee_ceiling(quoted_fee_wei: int) -> int:
    return (quoted_fee_wei * FEE_CEILING_BPS + FEE_BPS_DENOMINATOR - 1) // FEE_BPS_DENOMINATOR
