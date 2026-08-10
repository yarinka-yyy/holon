from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest

from holon_wallet.broadcast import MainnetTransferCode
from holon_wallet.model import ProfileSummary
from holon_wallet.transfer import PreparedTransferAction, UnsignedTransaction

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "modules" / "perpdex" / "src"))

from holon_perpdex.funding_contracts import FundingBundle  # noqa: E402
from holon_perpdex.funding_guard import FundingError, FundingGuardAdapter  # noqa: E402
from holon_perpdex.funding_profile import (  # noqa: E402
    ARBITRUM_CHAIN_ID, BRIDGE2_ADDRESS, NATIVE_USDC,
)
from holon_perpdex.funding_wallet import (  # noqa: E402
    FundingWalletAdapter, FundingWalletError,
)
from holon_wallet.module_funding import ModuleFundingExecutor  # noqa: E402

ACCOUNT = "0x" + "11" * 20
OPERATION_ID = "act-11111111-1111-4111-8111-111111111111"


def profile() -> ProfileSummary:
    return ProfileSummary("profile-main", "Main", ACCOUNT, "mnemonic", "", "")


class Preflight:
    def __init__(self, fee: int = 100) -> None:
        self.fee = fee
        self.calls: list[tuple[object, str]] = []

    def prepare(self, request, current, recipient):
        self.calls.append((request, recipient))
        return PreparedTransferAction(
            1, request.action_id, current.profile_id, current.label, current.address,
            recipient, "arbitrum", "Arbitrum One", ARBITRUM_CHAIN_ID, "usdc", "USDC",
            NATIVE_USDC, int(request.amount_atomic), 6,
            UnsignedTransaction(2, ARBITRUM_CHAIN_ID, 7, NATIVE_USDC, 0, "0x", 50_000, 2, 1),
            123, self.fee, request.created_at, request.expires_at,
        )


def bundle(tmp_path: Path, *, fee: int = 100):
    clock = [time.time()]
    guard = FundingGuardAdapter(Preflight(fee), clock=lambda: clock[0])
    guard.configure(tmp_path)
    account = {"address": ACCOUNT, "label": "Main"}
    preview = guard.preview("FUND_TRADING_ACCOUNT", {"amount_usdc": "5.25"}, account)
    return guard.prepare(
        OPERATION_ID, "FUND_TRADING_ACCOUNT", {"amount_usdc": "5.25"},
        account, preview.preview_digest,
    )


def test_funding_bundle_pins_only_native_usdc_arbitrum_bridge2(tmp_path: Path) -> None:
    item = bundle(tmp_path)
    phase = item.phases[0]

    assert item.intent.action_type.value == "FUND_TRADING_ACCOUNT"
    assert phase.semantic == {
        "amount_usdc": "5.25", "bridge_address": BRIDGE2_ADDRESS,
        "chain_id": 42161, "max_total_fee_wei": "100",
        "token_contract": NATIVE_USDC, "usd_atomic": "5250000",
    }
    tampered = item.to_mapping()
    tampered["phases"][0]["semantic"]["bridge_address"] = "0x" + "22" * 20
    with pytest.raises(Exception):
        FundingBundle.from_mapping(tampered)


def test_wallet_rechecks_fixed_route_and_fee_before_review(tmp_path: Path) -> None:
    item = bundle(tmp_path)
    adapter = FundingWalletAdapter()
    adapter.configure(tmp_path)
    prepared = adapter.prepare(
        item.to_mapping(), {"address": ACCOUNT, "label": "Main"}, profile(), Preflight(),
    )

    assert prepared.action.action_type == "perpdex_funding"
    assert prepared.action.protocol_id == "hyperliquid-bridge2"
    assert prepared.action.recipient == BRIDGE2_ADDRESS
    with pytest.raises(FundingWalletError) as changed:
        adapter.prepare(
            item.to_mapping(), {"address": ACCOUNT, "label": "Main"}, profile(),
            Preflight(101),
        )
    assert changed.value.code == "FUNDING_LIVE_STATE_CHANGED"

    class WrongRoute(Preflight):
        def prepare(self, request, current, recipient):
            return replace(super().prepare(request, current, recipient), chain_id=1)

    with pytest.raises(FundingWalletError) as wrong_route:
        adapter.prepare(
            item.to_mapping(), {"address": ACCOUNT, "label": "Main"}, profile(),
            WrongRoute(),
        )
    assert wrong_route.value.code == "FUNDING_LIVE_STATE_CHANGED"


def test_expired_funding_preview_and_invalid_amount_fail_before_wallet_review(tmp_path: Path) -> None:
    clock = [time.time()]
    guard = FundingGuardAdapter(Preflight(), clock=lambda: clock[0])
    guard.configure(tmp_path)
    account = {"address": ACCOUNT, "label": "Main"}
    with pytest.raises(FundingError) as too_small:
        guard.preview("FUND_TRADING_ACCOUNT", {"amount_usdc": "4.99"}, account)
    assert too_small.value.code == "FUNDING_INTENT_INVALID"

    preview = guard.preview("FUND_TRADING_ACCOUNT", {"amount_usdc": "5"}, account)
    clock[0] += 301
    with pytest.raises(FundingError) as expired:
        guard.prepare(
            OPERATION_ID, "FUND_TRADING_ACCOUNT", {"amount_usdc": "5"},
            account, preview.preview_digest,
        )
    assert expired.value.code == "FUNDING_PREVIEW_EXPIRED"


@dataclass
class History:
    records: list[object]

    def append(self, record):
        self.records.append(record)
        return tuple(self.records)


class Mainnet:
    def __init__(self, code: MainnetTransferCode) -> None:
        self.history_store = History([])
        self.code = code
        self.calls = 0

    def execute(self, action, digest, password, permit):
        self.calls += 1
        assert action.digest == digest and password == "local-password"
        return SimpleNamespace(
            code=self.code, transaction_hash="0x" + "ab" * 32,
        )


@pytest.mark.parametrize(
    ("code", "status"),
    [
        (MainnetTransferCode.PENDING, "PENDING_CREDIT"),
        (MainnetTransferCode.CONFIRMED, "PENDING_CREDIT"),
        (MainnetTransferCode.UNKNOWN, "UNKNOWN"),
    ],
)
def test_funding_submits_once_and_never_claims_hyperliquid_credit(
    tmp_path: Path, code: MainnetTransferCode, status: str,
) -> None:
    item = bundle(tmp_path)
    adapter = FundingWalletAdapter()
    adapter.configure(tmp_path)
    prepared = adapter.prepare(
        item.to_mapping(), {"address": ACCOUNT, "label": "Main"}, profile(), Preflight(),
    )
    adapter.mark_operation(item.operation_id, "AWAITING_LOCAL_CONFIRMATION")
    mainnet = Mainnet(code)

    result = ModuleFundingExecutor(mainnet, adapter).execute(prepared, "local-password")

    assert result.status == status and mainnet.calls == 1
    assert "refresh the public Hyperliquid portfolio" in result.message
    assert adapter.status(item.operation_id)["state"] == status
    stored = (tmp_path / "perpdex-operations.json").read_text(encoding="utf-8")
    assert "local-password" not in stored and "calldata" not in stored
