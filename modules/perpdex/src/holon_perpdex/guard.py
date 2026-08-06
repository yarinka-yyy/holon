"""Guard-side factories for verified PerpDEX capabilities."""

from __future__ import annotations

from .reader import HyperliquidReader


class GuardProtectedActionAdapter:
    """Marker implemented by the protected-action checkpoint."""

    adapter_version = "1"
    profile_id = "hyperliquid-mainnet-v1"


def create_reader() -> HyperliquidReader:
    return HyperliquidReader()


def create_protected_adapter() -> GuardProtectedActionAdapter:
    return GuardProtectedActionAdapter()


def create_earn_provider():
    from .earn import HlpEarnProvider

    return HlpEarnProvider(HyperliquidReader())
