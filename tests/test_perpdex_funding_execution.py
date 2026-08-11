from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import sys
from types import SimpleNamespace

from holon_wallet.broadcast import MainnetBroadcastPolicy, MainnetTransferCode, MainnetTransferExecutor, MainnetTransferResult
from holon_wallet.history import HistoryStatus, HistoryStore
from holon_wallet.storage import WalletPaths
from holon_wallet.transfer import (
    PreparedTransferAction, SigningPermit, TransferPreflightService, UnsignedTransaction,
)
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT / "modules" / "perpdex" / "src"))

from holon_perpdex.funding_guard import FundingGuardAdapter  # noqa: E402
from holon_perpdex.funding_profile import (  # noqa: E402
    ARBITRUM_CHAIN_ID, BRIDGE2_ADDRESS, NATIVE_USDC,
)
from holon_perpdex.funding_wallet import FundingWalletAdapter  # noqa: E402
from holon_wallet.module_funding import ModuleFundingExecutor  # noqa: E402

OPERATION_ID = "act-11111111-1111-4111-8111-111111111111"


class FundingExecutionRpc:
    """Deterministic Arbitrum RPC: it never contacts a public endpoint."""

    def __init__(self) -> None:
        self.send_calls = 0
        self.base_fee, self.priority_fee, self.gas_estimate = 10, 2, 50_000

    def chain_id(self): return ARBITRUM_CHAIN_ID
    def latest_block(self): return 300_000_000, self.base_fee
    def native_balance(self, _address): return 10**18
    def token_decimals(self, _contract): return 6
    def token_balance(self, _contract, _address): return 10_000_000
    def pending_nonce(self, _address): return 7
    def max_priority_fee_per_gas(self): return self.priority_fee
    def estimate_gas(self, _transaction): return self.gas_estimate

    def send_raw_transaction(self, raw_transaction):
        from web3 import Web3

        self.send_calls += 1
        return Web3.to_hex(Web3.keccak(raw_transaction))


class GuardPreflight:
    def __init__(self) -> None:
        self.calls = 0

    def prepare(self, request, current, recipient):
        self.calls += 1
        return PreparedTransferAction(
            1, request.action_id, current.profile_id, current.label, current.address,
            recipient, "arbitrum", "Arbitrum One", ARBITRUM_CHAIN_ID, "usdc", "USDC",
            NATIVE_USDC, int(request.amount_atomic), 6,
            UnsignedTransaction(2, ARBITRUM_CHAIN_ID, 7, NATIVE_USDC, 0, "0x", 50_000, 22, 1),
            123, 1_100_000, request.created_at, request.expires_at,
        )


def test_funding_executes_through_the_arbitrum_network_endpoint_only(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    password = "fixture-password"
    repository = VaultRepository(WalletPaths(tmp_path / "wallet"))
    record = repository.new_record(generate_mnemonic(), "Funding Fixture")
    repository.create_new(password, record)
    account = {"address": record.summary.address, "label": record.summary.label}
    guard = FundingGuardAdapter(GuardPreflight(), clock=lambda: now.timestamp())
    guard.configure(tmp_path)
    preview = guard.preview("FUND_TRADING_ACCOUNT", {"amount_usdc": "6"}, account)
    item = guard.prepare(
        OPERATION_ID, "FUND_TRADING_ACCOUNT", {"amount_usdc": "6"},
        account, preview.preview_digest,
    )
    rpc = FundingExecutionRpc()
    endpoints: list[str] = []

    def factory(endpoint: str):
        endpoints.append(endpoint)
        return rpc

    adapter = FundingWalletAdapter()
    adapter.configure(tmp_path)
    prepared = adapter.prepare(
        item.to_mapping(), account, record.summary,
        TransferPreflightService(factory, environ={"HOLON_ARBITRUM_RPC_URL": "fixture://arbitrum"}),
    )
    adapter.mark_operation(item.operation_id, "AWAITING_LOCAL_CONFIRMATION")
    executor = MainnetTransferExecutor(
        repository, HistoryStore(repository.paths), MainnetBroadcastPolicy.unavailable(),
        factory, {"HOLON_ARBITRUM_RPC_URL": "fixture://arbitrum"}, lambda: now,
    )
    rpc.base_fee = 11
    result = ModuleFundingExecutor(executor, adapter).execute(prepared, password)

    assert result.status == "PENDING_CREDIT"
    assert result.code == "FUNDING_BROADCAST_PENDING"
    assert rpc.send_calls == 1
    assert endpoints == ["fixture://arbitrum", "fixture://arbitrum"]
    assert prepared.action.max_total_fee_wei == 1_375_000

    rpc.base_fee = 12
    exceeded = executor.execute(
        prepared.action, prepared.action.digest, password, SigningPermit(),
    )
    assert exceeded.code.value == "REVALIDATION_FEE_CAP_EXCEEDED"
    assert rpc.send_calls == 1


def test_pre_sign_funding_refusal_changes_history_from_prepared_to_failed(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    phase = SimpleNamespace(phase_id="phase-fixture", phase_type=SimpleNamespace(value="ARBITRUM_USDC_TRANSFER"))
    bundle = SimpleNamespace(operation_id=OPERATION_ID, intent=SimpleNamespace(action_type=SimpleNamespace(value="FUND_TRADING_ACCOUNT")), phases=(phase,))
    action = SimpleNamespace(action_id="act-history", profile_id="profile", network_id="arbitrum", chain_id=42161,
        sender="0x" + "11" * 20, recipient=BRIDGE2_ADDRESS, token_contract=NATIVE_USDC, token="USDC",
        amount_atomic=6_000_000, decimals=6, max_total_fee_wei=100, created_at=now, digest="a" * 64)
    events: list[tuple[str, str]] = []

    class Adapter:
        def mark_operation(self, operation_id, state): events.append((operation_id, state))
        def mark_phase(self, operation_id, phase_id, state, **_kwargs): events.append((phase_id, state))
        def mark_external_submission_started(self, _operation_id): raise AssertionError("must not sign")

    class Refusal:
        def __init__(self, store): self.history_store = store
        def execute(self, prepared, digest, password, permit, on_signed=None):
            del prepared, digest, password, permit, on_signed
            return MainnetTransferResult(MainnetTransferCode.REVALIDATION_FAILED, "act-history", "a" * 64,
                "", "", None, "2026-08-11T12:00:00Z", False, True, False, "perpdex_funding")

    store = HistoryStore(WalletPaths(tmp_path))
    result = ModuleFundingExecutor(Refusal(store), Adapter()).execute(SimpleNamespace(bundle=bundle, action=action), "fixture")
    assert result.code == "FUNDING_REVALIDATION_FAILED"
    assert store.load()[0].status is HistoryStatus.FAILED
    assert store.load()[0].transaction_hash is None
    assert events == [(OPERATION_ID, "EXECUTING"), ("phase-fixture", "SUBMITTING"),
                      ("phase-fixture", "FAILED"), (OPERATION_ID, "FAILED")]
