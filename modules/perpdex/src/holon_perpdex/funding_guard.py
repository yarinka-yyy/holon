"""Guard-side live preview for the only supported Hyperliquid funding route."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal
import hashlib
import time

from holon_wallet.model import ProfileSummary
from holon_wallet.transfer import (
    PendingTransferRequest, TransferPreflightError, TransferPreflightService,
)

from .contracts import ActionType, ContractError, PerpDexActionIntent, PerpDexActionPreview, PhaseType, ProtectedActionPhase, digest_json
from .funding_contracts import FundingBundle
from .funding_profile import (
    ACTION_TYPE, ARBITRUM_CHAIN_ID, ARBITRUM_NETWORK_ID, BRIDGE2_ADDRESS,
    NATIVE_USDC, REVIEW_SECONDS,
)
from .persistence import PerpDexOperationStore


class FundingError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class FundingGuardAdapter:
    adapter_version = "1"
    profile_id = "hyperliquid-arbitrum-funding-v1"
    wallet_capability_id = "holon.perpdex.funding.wallet"

    def __init__(self, preflight: TransferPreflightService | None = None, *, clock=None) -> None:
        self._preflight = preflight or TransferPreflightService()
        self._clock = clock or time.time
        self._operations: PerpDexOperationStore | None = None
        self._previews: dict[str, tuple[PerpDexActionIntent, int, float]] = {}

    def configure(self, data_dir) -> None:
        self._operations = PerpDexOperationStore(data_dir / "perpdex-operations.json", clock=self._clock)
        self._operations.contain_stale()

    def preview(self, action_type: object, params: Mapping[str, object], account: Mapping[str, str]) -> PerpDexActionPreview:
        intent, action = self._quote(action_type, params, account)
        preview = {
            "amount_usdc": intent.amount_usdc,
            "bridge_address": BRIDGE2_ADDRESS,
            "chain_id": 42161,
            "max_total_fee_wei": str(action.max_total_fee_wei),
            "network": "Arbitrum One",
            "token_contract": NATIVE_USDC,
        }
        digest = digest_json({"account": account["address"].lower(), "intent": intent.to_mapping(), "preview": preview})
        expires = self._timestamp(self._clock() + REVIEW_SECONDS)
        self._previews = {key: value for key, value in self._previews.items() if value[2] > self._clock()}
        self._previews[digest] = (
            intent, action.max_total_fee_wei, self._clock() + REVIEW_SECONDS,
        )
        return PerpDexActionPreview(
            "PREVIEW_READY", ActionType.FUND_TRADING_ACCOUNT,
            {"address": account["address"].lower(), "label": account.get("label", "")},
            preview, digest, expires,
            ("ARBITRUM_CHAIN_VERIFIED", "NATIVE_USDC_VERIFIED", "BALANCE_VERIFIED", "GAS_VERIFIED"),
            ("BRIDGE_DEPOSIT_IRREVERSIBLE", "HYPERLIQUID_CREDIT_PENDING"),
            "MODULE_ACTION_PREVIEW_READY", "Hyperliquid funding preview is ready.",
        )

    def prepare(
        self, operation_id: str, action_type: object, params: Mapping[str, object],
        account: Mapping[str, str], preview_digest: str,
    ) -> FundingBundle:
        cached = self._previews.pop(preview_digest, None)
        if cached is None or cached[2] <= self._clock():
            raise FundingError("FUNDING_PREVIEW_EXPIRED")
        intent, action = self._quote(action_type, params, account)
        if intent != cached[0] or action.max_total_fee_wei > cached[1]:
            raise FundingError("FUNDING_LIVE_STATE_CHANGED")
        created = self._timestamp(self._clock())
        expires = self._timestamp(self._clock() + REVIEW_SECONDS)
        semantic = {
            "amount_usdc": intent.amount_usdc, "bridge_address": BRIDGE2_ADDRESS,
            "chain_id": 42161, "max_total_fee_wei": str(action.max_total_fee_wei),
            "token_contract": NATIVE_USDC, "usd_atomic": str(action.amount_atomic),
        }
        phase = ProtectedActionPhase(
            "phase-" + hashlib.sha256(operation_id.encode()).hexdigest()[:32],
            PhaseType.ARBITRUM_USDC_TRANSFER, str(action.transaction.nonce), expires,
            semantic, digest_json(semantic), None,
        )
        provisional = FundingBundle(
            operation_id, account["address"].lower(), intent,
            digest_json({"block": action.block_number, "fee": semantic["max_total_fee_wei"]}),
            created, expires, (phase,), "0" * 64,
        )
        bundle = FundingBundle(
            provisional.operation_id, provisional.account, provisional.intent,
            provisional.snapshot_digest, provisional.created_at, provisional.expires_at,
            provisional.phases, digest_json(provisional.material_mapping()),
            provisional.disclosure, provisional.profile_id, provisional.profile_version,
            provisional.profile_digest, provisional.bundle_version,
        )
        if self._operations is None:
            raise FundingError("FUNDING_OPERATION_STATE_UNAVAILABLE")
        self._operations.begin(bundle)
        return bundle

    def mark_awaiting_confirmation(self, operation_id: str) -> None:
        self._store().mark_operation(operation_id, "AWAITING_LOCAL_CONFIRMATION")

    def mark_operation(self, operation_id: str, state: str) -> None:
        self._store().mark_operation(operation_id, state)

    def mark_phase(self, operation_id: str, phase_id: str, state: str, **kwargs) -> None:
        self._store().mark_phase(operation_id, phase_id, state, **kwargs)

    def reject(self, operation_id: str) -> None:
        self._store().mark_operation(operation_id, "REJECTED")

    def status(self, operation_id: str):
        return self._store().status(operation_id)

    def _quote(self, action_type: object, params: Mapping[str, object], account: Mapping[str, str]):
        try:
            intent = PerpDexActionIntent.from_mapping(action_type, params)
            if intent.action_type is not ActionType.FUND_TRADING_ACCOUNT:
                raise ContractError("Invalid funding action")
            address = str(account["address"])
            profile = ProfileSummary("funding-preview", str(account.get("label", "Wallet")), address, "mnemonic", "", "")
            now = datetime.fromtimestamp(self._clock(), UTC)
            amount_atomic = int(Decimal(intent.amount_usdc) * Decimal(1_000_000))
            request = PendingTransferRequest(
                "funding-preview", profile.profile_id, now,
                now + timedelta(seconds=REVIEW_SECONDS), ARBITRUM_NETWORK_ID,
                "usdc", amount_atomic,
            )
            action = self._preflight.prepare(request, profile, BRIDGE2_ADDRESS)
            if (
                action.chain_id != ARBITRUM_CHAIN_ID
                or action.token_contract is None
                or action.token_contract.lower() != NATIVE_USDC.lower()
                or action.decimals != 6
                or action.recipient.lower() != BRIDGE2_ADDRESS.lower()
            ):
                raise FundingError("FUNDING_PREFLIGHT_INVALID")
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
