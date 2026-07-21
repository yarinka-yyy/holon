from __future__ import annotations

from concurrent.futures import Executor, Future

from holon_wallet.public_data import (
    AssetBalance,
    NetworkSnapshot,
    PortfolioSnapshot,
    PublicDataStatus,
)


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
