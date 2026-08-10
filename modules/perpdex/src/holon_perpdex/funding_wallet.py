"""Wallet-side verifier and exact EVM preflight for Hyperliquid funding."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime

from holon_wallet.model import ProfileSummary
from holon_wallet.transfer import PendingTransferRequest, TransferPreflightError, TransferPreflightService

from .funding_contracts import FundingBundle
from .funding_profile import (
    ARBITRUM_CHAIN_ID, ARBITRUM_NETWORK_ID, BRIDGE2_ADDRESS, NATIVE_USDC,
)
from .persistence import PerpDexOperationStore


class FundingWalletError(RuntimeError):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class FundingPreparedBundle:
    bundle: FundingBundle
    action: object

    @property
    def operation_id(self): return self.bundle.operation_id

    @property
    def account(self): return self.bundle.account

    @property
    def intent(self): return self.bundle.intent

    @property
    def created_at(self): return self.bundle.created_at

    @property
    def expires_at(self): return self.bundle.expires_at

    @property
    def phases(self): return self.bundle.phases

    @property
    def disclosure(self): return self.bundle.disclosure

    @property
    def bundle_digest(self): return self.bundle.bundle_digest

    def to_mapping(self): return self.bundle.to_mapping()


class FundingWalletAdapter:
    adapter_version = "1"
    profile_id = "hyperliquid-arbitrum-funding-v1"

    def __init__(self) -> None:
        self._operations: PerpDexOperationStore | None = None

    def configure(self, data_dir) -> None:
        self._operations = PerpDexOperationStore(data_dir / "perpdex-operations.json")
        self._operations.contain_stale()

    def verify(self, raw_bundle: Mapping[str, object], account: Mapping[str, str]) -> FundingBundle:
        try:
            bundle = FundingBundle.from_mapping(raw_bundle)
        except Exception as exc:
            raise FundingWalletError("FUNDING_BUNDLE_INVALID") from exc
        if bundle.account.lower() != str(account.get("address", "")).lower():
            raise FundingWalletError("FUNDING_ACCOUNT_CHANGED")
        return bundle

    def prepare(
        self, raw_bundle: Mapping[str, object], account: Mapping[str, str],
        profile: ProfileSummary, preflight: TransferPreflightService,
    ) -> FundingPreparedBundle:
        bundle = self.verify(raw_bundle, account)
        if profile.address.lower() != bundle.account.lower():
            raise FundingWalletError("FUNDING_ACCOUNT_CHANGED")
        try:
            created = datetime.fromisoformat(bundle.created_at.removesuffix("Z") + "+00:00")
            expires = datetime.fromisoformat(bundle.expires_at.removesuffix("Z") + "+00:00")
            action = preflight.prepare(PendingTransferRequest(
                bundle.operation_id, profile.profile_id, created, expires,
                ARBITRUM_NETWORK_ID, "usdc", int(bundle.phases[0].semantic["usd_atomic"]),
            ), profile, BRIDGE2_ADDRESS)
        except TransferPreflightError as exc:
            raise FundingWalletError("FUNDING_" + exc.code.value) from exc
        except Exception as exc:
            raise FundingWalletError("FUNDING_PREFLIGHT_INVALID") from exc
        semantic = bundle.phases[0].semantic
        if (
            action.network_id != ARBITRUM_NETWORK_ID
            or action.chain_id != ARBITRUM_CHAIN_ID
            or action.asset_id != "usdc" or action.decimals != 6
            or action.token_contract is None
            or action.token_contract.lower() != NATIVE_USDC.lower()
            or action.recipient.lower() != BRIDGE2_ADDRESS.lower()
        ):
            raise FundingWalletError("FUNDING_WALLET_ROUTE_CHANGED")
        if action.amount_atomic != int(semantic["usd_atomic"]):
            raise FundingWalletError("FUNDING_AMOUNT_CHANGED")
        if action.max_total_fee_wei > int(semantic["max_total_fee_wei"]):
            raise FundingWalletError("FUNDING_WALLET_FEE_CAP_EXCEEDED")
        return FundingPreparedBundle(
            bundle, replace(
                action, action_type="perpdex_funding", method="bridge2_deposit",
                protocol_id="hyperliquid-bridge2",
            ),
        )

    def mark_operation(self, operation_id: str, state: str) -> None:
        self._store().mark_operation(operation_id, state)

    def mark_phase(self, operation_id: str, phase_id: str, state: str, **kwargs) -> None:
        self._store().mark_phase(operation_id, phase_id, state, **kwargs)

    def status(self, operation_id: str):
        return self._store().status(operation_id)

    def _store(self) -> PerpDexOperationStore:
        if self._operations is None:
            raise FundingWalletError("FUNDING_OPERATION_STATE_UNAVAILABLE")
        return self._operations


def create_funding_protected_adapter() -> FundingWalletAdapter:
    return FundingWalletAdapter()
