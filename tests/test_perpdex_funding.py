from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from types import SimpleNamespace
import sys
import time

import pytest

from holon_contracts import ContractViolation, MessageKind, make_envelope
from holon_guard.authority import AuthorityService
from holon_wallet.broadcast import MainnetTransferCode
from holon_wallet.model import ProfileSummary
from holon_wallet.transfer import (
    PreparedTransferAction, UnsignedTransaction,
)

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
from holon_perpdex.persistence import PerpDexOperationStore  # noqa: E402
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
            UnsignedTransaction(2, ARBITRUM_CHAIN_ID, 7, NATIVE_USDC, 0, "0x", 1, self.fee, 1),
            123, self.fee, request.created_at, request.expires_at,
        )


class SequencedPreflight(Preflight):
    def __init__(self, *fees: int) -> None:
        if not fees:
            raise ValueError("At least one fee is required")
        super().__init__(fees[0])
        self._fees = iter(fees)

    def prepare(self, request, current, recipient):
        self.fee = next(self._fees)
        return super().prepare(request, current, recipient)


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
        "chain_id": 42161, "max_total_fee_wei": "125",
        "token_contract": NATIVE_USDC, "usd_atomic": "5250000",
    }
    tampered = item.to_mapping()
    tampered["phases"][0]["semantic"]["bridge_address"] = "0x" + "22" * 20
    with pytest.raises(Exception):
        FundingBundle.from_mapping(tampered)


def test_v2_funding_operation_migrates_terminal_diagnostics(tmp_path: Path) -> None:
    item = bundle(tmp_path)
    path = tmp_path / "operations.json"
    store = PerpDexOperationStore(path)
    store.begin(item)
    store.mark_operation(item.operation_id, "AWAITING_LOCAL_CONFIRMATION")
    store.mark_phase(
        item.operation_id, item.phases[0].phase_id, "SUBMITTING", code="SUBMITTING",
    )
    store.mark_phase(
        item.operation_id, item.phases[0].phase_id, "FAILED",
        code="FUNDING_REVALIDATION_FAILED",
    )
    store.mark_operation(item.operation_id, "FAILED")

    previous = json.loads(path.read_text(encoding="utf-8"))
    previous["operations_version"] = "2"
    for field in (
        "failure_category", "operation_class", "terminal_code", "terminal_stage",
    ):
        del previous["operations"][0][field]
    path.write_text(json.dumps(previous), encoding="utf-8")

    migrated = PerpDexOperationStore(path).status(item.operation_id)
    assert migrated["terminal_code"] == "FUNDING_REVALIDATION_FAILED"
    assert migrated["terminal_stage"] == "PHASE_ARBITRUM_USDC_TRANSFER"
    assert '"operations_version":"3"' in path.read_text(encoding="utf-8")


def test_funding_preview_serializes_without_raw_contract_fields(tmp_path: Path) -> None:
    guard = FundingGuardAdapter(Preflight())
    guard.configure(tmp_path)
    preview = guard.preview(
        "FUND_TRADING_ACCOUNT", {"amount_usdc": "6"},
        {"address": ACCOUNT, "label": "Main"},
    )
    payload = AuthorityService._module_preview_payload(
        "holon.perpdex", "holon.perpdex.funding.guard", "FUND_TRADING_ACCOUNT",
        preview=preview, execution_available=True,
    )
    envelope = make_envelope(MessageKind.MODULE_ACTION_PREVIEW, payload)

    assert envelope.payload["preview"]["native_usdc_address"] == NATIVE_USDC
    with pytest.raises(ContractViolation):
        make_envelope(MessageKind.MODULE_ACTION_PREVIEW, {
            **payload,
            "preview": {**payload["preview"], "token_contract": NATIVE_USDC},
        })


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
            Preflight(126),
        )
    assert changed.value.code == "FUNDING_WALLET_FEE_CAP_EXCEEDED"

    class WrongRoute(Preflight):
        def prepare(self, request, current, recipient):
            return replace(super().prepare(request, current, recipient), chain_id=1)

    with pytest.raises(FundingWalletError) as wrong_route:
        adapter.prepare(
            item.to_mapping(), {"address": ACCOUNT, "label": "Main"}, profile(),
            WrongRoute(),
        )
    assert wrong_route.value.code == "FUNDING_WALLET_ROUTE_CHANGED"

    class WrongAmount(Preflight):
        def prepare(self, request, current, recipient):
            return replace(
                super().prepare(request, current, recipient),
                amount_atomic=int(request.amount_atomic) + 1,
            )

    with pytest.raises(FundingWalletError) as wrong_amount:
        adapter.prepare(
            item.to_mapping(), {"address": ACCOUNT, "label": "Main"}, profile(),
            WrongAmount(),
        )
    assert wrong_amount.value.code == "FUNDING_AMOUNT_CHANGED"


def test_fee_drift_within_ceiling_reaches_wallet_preparation(tmp_path: Path) -> None:
    preflight = SequencedPreflight(100, 102, 103)
    guard = FundingGuardAdapter(preflight)
    guard.configure(tmp_path)
    account = {"address": ACCOUNT, "label": "Main"}

    preview = guard.preview("FUND_TRADING_ACCOUNT", {"amount_usdc": "6"}, account)
    bundle = guard.prepare(
        OPERATION_ID, "FUND_TRADING_ACCOUNT", {"amount_usdc": "6"},
        account, preview.preview_digest,
    )
    adapter = FundingWalletAdapter()
    adapter.configure(tmp_path)
    prepared = adapter.prepare(bundle.to_mapping(), account, profile(), preflight)

    assert preview.preview["max_total_fee_wei"] == "125"
    assert bundle.phases[0].semantic["max_total_fee_wei"] == "125"
    assert prepared.action.max_total_fee_wei == 125


def test_guard_refuses_fee_above_preview_ceiling_before_wallet(tmp_path: Path) -> None:
    guard = FundingGuardAdapter(SequencedPreflight(100, 126))
    guard.configure(tmp_path)
    account = {"address": ACCOUNT, "label": "Main"}
    preview = guard.preview("FUND_TRADING_ACCOUNT", {"amount_usdc": "6"}, account)

    with pytest.raises(FundingError) as failed:
        guard.prepare(
            OPERATION_ID, "FUND_TRADING_ACCOUNT", {"amount_usdc": "6"},
            account, preview.preview_digest,
        )

    assert failed.value.code == "FUNDING_GUARD_FEE_CAP_EXCEEDED"


def test_guard_binds_preview_to_account_and_amount(tmp_path: Path) -> None:
    guard = FundingGuardAdapter(Preflight())
    guard.configure(tmp_path)
    account = {"address": ACCOUNT, "label": "Main"}
    preview = guard.preview("FUND_TRADING_ACCOUNT", {"amount_usdc": "6"}, account)

    with pytest.raises(FundingError) as wrong_account:
        guard.prepare(
            OPERATION_ID, "FUND_TRADING_ACCOUNT", {"amount_usdc": "6"},
            {"address": "0x" + "22" * 20, "label": "Other"}, preview.preview_digest,
        )
    assert wrong_account.value.code == "FUNDING_ACCOUNT_CHANGED"

    preview = guard.preview("FUND_TRADING_ACCOUNT", {"amount_usdc": "6"}, account)
    with pytest.raises(FundingError) as wrong_amount:
        guard.prepare(
            OPERATION_ID, "FUND_TRADING_ACCOUNT", {"amount_usdc": "6.01"},
            account, preview.preview_digest,
        )
    assert wrong_amount.value.code == "FUNDING_AMOUNT_CHANGED"


def test_guard_rechecks_the_fixed_route(tmp_path: Path) -> None:
    class WrongRoute(Preflight):
        def prepare(self, request, current, recipient):
            return replace(super().prepare(request, current, recipient), asset_id="eth")

    guard = FundingGuardAdapter(WrongRoute())
    guard.configure(tmp_path)

    with pytest.raises(FundingError) as wrong_route:
        guard.preview(
            "FUND_TRADING_ACCOUNT", {"amount_usdc": "6"},
            {"address": ACCOUNT, "label": "Main"},
        )

    assert wrong_route.value.code == "FUNDING_GUARD_ROUTE_CHANGED"


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

    def execute(self, action, digest, password, permit, *, on_signed=None):
        self.calls += 1
        assert action.digest == digest and password == "local-password"
        if on_signed is not None:
            on_signed()
        return SimpleNamespace(
            code=self.code,
            transaction_hash="0x" + "ab" * 32,
            history_status=object(),
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
