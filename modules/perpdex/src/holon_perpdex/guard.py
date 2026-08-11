"""Guard-side factories for verified PerpDEX capabilities."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
import time

from .actions import AdapterError, BuiltPreview, HyperliquidActionBuilder
from .contracts import (
    ActionType, ContractError, PerpDexActionIntent, PerpDexActionPreview,
    ProtectedActionBundle,
)
from .persistence import PersistenceError, PerpDexNonceStore, PerpDexOperationStore
from .reader import HyperliquidReader

DIRECT_REVIEW_MAX_AGE_SECONDS = 5.0


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
        self._previews: dict[str, tuple[BuiltPreview, float, float]] = {}

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
        created = self.clock()
        expires = created + built.intent.review_seconds
        self._previews = {
            key: value for key, value in self._previews.items() if value[1] > self.clock()
        }
        self._previews[preview.preview_digest] = (built, expires, created)
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
        try:
            requested = PerpDexActionIntent.from_mapping(action_type, params)
            checked_account = self._builder._account(account)
        except (ContractError, AdapterError) as exc:
            raise AdapterError(
                "PERPDEX_PREVIEW_MISMATCH", "PerpDEX preview does not match the request",
            ) from exc
        if (
            previous.intent != requested
            or previous.account["address"] != checked_account["address"]
        ):
            raise AdapterError("PERPDEX_PREVIEW_MISMATCH", "PerpDEX preview does not match the request")
        if requested.action_type in {ActionType.OPEN_POSITION, ActionType.CLOSE_POSITION}:
            if self.clock() - cached[2] > DIRECT_REVIEW_MAX_AGE_SECONDS:
                raise AdapterError("PERPDEX_PREVIEW_EXPIRED", "PerpDEX preview expired")
            current = previous
        else:
            current = self._builder.preview(action_type, params, checked_account)
        try:
            bundle = self._builder.bundle(operation_id, current)
        except PersistenceError as exc:
            code = (
                "PERPDEX_NONCE_STATE_UNAVAILABLE"
                if exc.code == "PERPDEX_PERSISTENCE_UNAVAILABLE"
                else "PERPDEX_NONCE_STATE_INVALID"
            )
            raise AdapterError(code, "PerpDEX nonce state is unavailable") from exc
        if self._operations is None:
            raise AdapterError("PERPDEX_OPERATION_STATE_UNAVAILABLE", "PerpDEX operation state is unavailable")
        try:
            self._operations.begin(bundle)
        except PersistenceError as exc:
            code = (
                "PERPDEX_OPERATION_STATE_UNAVAILABLE"
                if exc.code == "PERPDEX_PERSISTENCE_UNAVAILABLE"
                else "PERPDEX_OPERATION_STATE_INVALID"
            )
            raise AdapterError(code, "PerpDEX operation state is unavailable") from exc
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
