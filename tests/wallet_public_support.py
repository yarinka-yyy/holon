from __future__ import annotations

from concurrent.futures import Executor, Future

from web3 import Web3

from holon_wallet.broadcast import (
    BASE_RPC_ENV,
    BroadcastReceiptTracker,
    MainnetBroadcastPolicy,
    MainnetTransferExecutor,
)
from holon_wallet.history import HistoryStore
from holon_wallet.public_data import (
    AssetBalance,
    NetworkSnapshot,
    PortfolioSnapshot,
    PublicDataStatus,
)
from holon_wallet.prices import AssetPrice, PriceSnapshot, PriceStatus
from holon_wallet.transfer import BASE_CHAIN_ID, TransferPreflightService, transfer_route
from holon_wallet.signer import OfflineSigningPolicy


class ImmediateExecutor(Executor):
    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)
        return future

    def shutdown(self, wait=True, *, cancel_futures=False):
        return


class DeferredExecutor(Executor):
    def __init__(self) -> None:
        self.tasks = []

    def submit(self, fn, /, *args, **kwargs):
        future = Future()
        self.tasks.append((future, fn, args, kwargs))
        return future

    def run_next(self) -> None:
        future, fn, args, kwargs = self.tasks.pop(0)
        if future.cancelled():
            return
        try:
            future.set_result(fn(*args, **kwargs))
        except BaseException as error:
            future.set_exception(error)

    def shutdown(self, wait=True, *, cancel_futures=False):
        if cancel_futures:
            for future, _fn, _args, _kwargs in self.tasks:
                future.cancel()


class StubPublicDataService:
    def __init__(
        self,
        statuses: dict[str, PublicDataStatus] | None = None,
    ) -> None:
        self.statuses = statuses or {
            "ethereum": PublicDataStatus.LIVE,
            "base": PublicDataStatus.LIVE,
        }
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def refresh(
        self, profile_id: str, address: str, network_ids: tuple[str, ...],
    ) -> PortfolioSnapshot:
        self.calls.append((profile_id, address, network_ids))
        snapshots = tuple(
            public_snapshot(network_id, self.statuses[network_id])
            for network_id in network_ids
        )
        return PortfolioSnapshot(profile_id, address, snapshots)


class StubPriceService:
    def __init__(self, status: PriceStatus = PriceStatus.LIVE) -> None:
        self.status = status
        self.calls = 0

    def refresh(self) -> PriceSnapshot:
        self.calls += 1
        answer = 250_000_000_000 if self.status is PriceStatus.LIVE else None
        usdc_answer = 100_000_000 if self.status is PriceStatus.LIVE else None
        return PriceSnapshot(
            8453,
            self.status,
            (
                AssetPrice("eth", "ETH", self.status, answer, 8 if answer else None, 1),
                AssetPrice(
                    "usdc", "USDC", self.status,
                    usdc_answer, 8 if usdc_answer else None, 1,
                ),
            ),
            1,
            None if self.status is PriceStatus.LIVE else "UNAVAILABLE",
        )


class StubTransferRpc:
    def __init__(self) -> None:
        self.observed_chain_id = BASE_CHAIN_ID
        self.estimated_transaction: dict[str, object] | None = None

    def chain_id(self) -> int:
        return self.observed_chain_id

    def latest_block(self) -> tuple[int, int]:
        return 12_345_678, 10_000_000

    def native_balance(self, _address: str) -> int:
        return 10**18

    def token_decimals(self, _contract: str) -> int:
        return 6

    def token_balance(self, _contract: str, _address: str) -> int:
        return 2_500_000

    def pending_nonce(self, _address: str) -> int:
        return 4

    def max_priority_fee_per_gas(self) -> int:
        return 1_000_000

    def estimate_gas(self, transaction) -> int:
        self.estimated_transaction = dict(transaction)
        return 55_000


class StubMainnetRpc(StubTransferRpc):
    def __init__(self) -> None:
        super().__init__()
        self.send_calls = 0
        self.receipt = None
        self.public_transaction = None

    def send_raw_transaction(self, raw_transaction) -> str:
        self.send_calls += 1
        return Web3.to_hex(Web3.keccak(raw_transaction))

    def transaction(self, _transaction_hash):
        return self.public_transaction

    def transaction_receipt(self, _transaction_hash):
        return self.receipt


def mainnet_services(repository, history_store: HistoryStore, enabled: bool = True):
    rpc = StubMainnetRpc()
    policy = MainnetBroadcastPolicy(
        enabled,
        OfflineSigningPolicy(10**18 if enabled else None),
    )
    environ = {BASE_RPC_ENV: "fixture://base"}
    executor = MainnetTransferExecutor(
        repository,
        history_store,
        policy,
        lambda _endpoint: rpc,
        environ,
    )
    tracker = BroadcastReceiptTracker(
        history_store,
        lambda _endpoint: rpc,
        environ,
        timeout_seconds=0,
    )
    return executor, tracker, rpc


class StubTransferPreflightService:
    def __init__(self, error: BaseException | None = None) -> None:
        self.error = error
        self.rpc = StubTransferRpc()
        self.calls = []
        self._service = TransferPreflightService(lambda _endpoint: self.rpc, environ={})

    def prepare(self, request, profile, recipient):
        self.calls.append((request, profile, recipient))
        if self.error is not None:
            raise self.error
        self.rpc.observed_chain_id = transfer_route(
            request.network_id, request.asset_id,
        ).chain_id
        return self._service.prepare(request, profile, recipient)

    def quote_maximum_native(self, profile, network_id, recipient):
        self.calls.append(("maximum", profile, network_id, recipient))
        if self.error is not None:
            raise self.error
        self.rpc.observed_chain_id = transfer_route(network_id, "eth").chain_id
        return self._service.quote_maximum_native(profile, network_id, recipient)


def public_snapshot(
    network_id: str,
    status: PublicDataStatus = PublicDataStatus.LIVE,
    *,
    eth: int = 10**18,
    usdc: int = 2_500_000,
) -> NetworkSnapshot:
    label, chain_id = (
        ("Ethereum", 1) if network_id == "ethereum" else ("Base", 8453)
    )
    if status is PublicDataStatus.UNAVAILABLE:
        return NetworkSnapshot(
            network_id, label, chain_id, status, None, None, None, None,
            "RPC_UNAVAILABLE",
        )
    return NetworkSnapshot(
        network_id,
        label,
        chain_id,
        status,
        123,
        AssetBalance("ETH", eth, 18),
        AssetBalance("USDC", usdc, 6),
        "2026-07-20T12:00:00Z",
    )
