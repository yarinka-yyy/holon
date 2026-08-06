"""Wallet-side factories for the bounded PerpDEX module surface."""

from __future__ import annotations


class WalletProtectedActionAdapter:
    """Marker implemented by the protected-action checkpoint."""

    adapter_version = "1"
    profile_id = "hyperliquid-mainnet-v1"


def create_protected_adapter() -> WalletProtectedActionAdapter:
    return WalletProtectedActionAdapter()


def create_earn_provider():
    from .earn import HlpEarnProvider
    from .reader import HyperliquidReader

    return HlpEarnProvider(HyperliquidReader())


def create_page_model() -> dict[str, str]:
    return {
        "body": "Hyperliquid public data and protected actions.",
        "moduleId": "holon.perpdex",
        "title": "PerpDEX",
    }
