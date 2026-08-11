"""Guard-side factories for verified PerpDEX capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import time

from .actions import AdapterError, BuiltPreview, HyperliquidActionBuilder
from .contracts import PerpDexActionPreview, ProtectedActionBundle
from .persistence import PerpDexNonceStore, PerpDexOperationStore
from .reader import HyperliquidReader


class GuardProtectedActionAdapter:
    """Live preview and exact bundle builder owned by the Guard process."""

    adapter_version = "1"
    profile_id = "hyperliquid-mainnet-v1"
    wallet_capability_id = "holon.perpdex.action.wallet"

    def __init__(self, reader: HyperliquidReader | None = None, *, clock=None) -> None:
        self.reader = reader or HyperliquidReader()
        self.clock = clock or time.time
        self._builder = HyperliquidActionBuilder(self.reader, clock=self.clock)
        self._operations: PerpDexOperationStore | None = None
        self._previews: dict[str, tuple[BuiltPreview, float]] = {}

    def configure(self, data_dir: Path) -> None:
        root = Path(data_dir)
        self._builder.nonce_store = PerpDexNonceStore(
            root / "perpdex-nonces.json",
            clock_ms=lambda: int(self.clock() * 1000),
        )
        self._operations = PerpDexOperationStore(
            root / "perpdex-operations.json", clock=self.clock,
        )
        self._operations.contain_stale()
        self._operations.prune_transient()

    def preview(
        self, action_type: object, params: Mapping[str, object],
        account: Mapping[str, str],
    ) -> PerpDexActionPreview:
        built = self._builder.preview(action_type, params, account)
        preview = self._builder.public_preview(built)
        assert preview.preview_digest is not None and preview.expires_at is not None
        expires = self.clock() + built.intent.review_seconds
        self._previews = {
            key: value for key, value in self._previews.items() if value[1] > self.clock()
        }
        self._previews[preview.preview_digest] = (built, expires)
        return preview

    def prepare(
        self, operation_id: str, action_type: object,
        params: Mapping[str, object], account: Mapping[str, str],
        preview_digest: str,
    ) -> ProtectedActionBundle:
        cached = self._previews.pop(preview_digest, None)
        if cached is None or cached[1] <= self.clock():
            raise AdapterError("PERPDEX_PREVIEW_EXPIRED", "PerpDEX preview expired")
        previous = cached[0]
        current = self._builder.preview(action_type, params, account)
        if (
            previous.intent != current.intent
            or previous.account["address"] != current.account["address"]
        ):
            raise AdapterError("PERPDEX_PREVIEW_MISMATCH", "PerpDEX preview does not match the request")
        bundle = self._builder.bundle(operation_id, current)
        if self._operations is None:
            raise AdapterError("PERPDEX_OPERATION_STATE_UNAVAILABLE", "PerpDEX operation state is unavailable")
        self._operations.begin(bundle)
        return bundle

    def mark_awaiting_confirmation(self, operation_id: str) -> None:
        if self._operations is None:
            raise AdapterError("PERPDEX_OPERATION_STATE_UNAVAILABLE", "PerpDEX operation state is unavailable")
        self._operations.mark_operation(operation_id, "AWAITING_LOCAL_CONFIRMATION")

    def reject(self, operation_id: str) -> None:
        if self._operations is not None:
            self._operations.mark_operation(operation_id, "REJECTED")

    def status(self, operation_id: str) -> Mapping[str, object] | None:
        return None if self._operations is None else self._operations.status(operation_id)


def create_reader() -> HyperliquidReader:
    return HyperliquidReader()


def create_protected_adapter() -> GuardProtectedActionAdapter:
    return GuardProtectedActionAdapter()


def create_earn_provider():
    from .earn import HlpEarnProvider

    return HlpEarnProvider(HyperliquidReader())
