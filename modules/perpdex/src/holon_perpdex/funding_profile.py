"""Immutable Arbitrum-native-USDC profile for Hyperliquid account funding."""

from __future__ import annotations

import hashlib
import json

PROFILE_ID = "hyperliquid-arbitrum-funding-v1"
PROFILE_VERSION = "2"
ACTION_TYPE = "FUND_TRADING_ACCOUNT"
REVIEW_SECONDS = 300
MIN_AMOUNT_ATOMIC = 5_000_000
FEE_CEILING_BPS = 12_500
FEE_BPS_DENOMINATOR = 10_000
ARBITRUM_CHAIN_ID = 42161
ARBITRUM_NETWORK_ID = "arbitrum"
NATIVE_USDC = "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
BRIDGE2_ADDRESS = "0x2df1c51e09aecf9cacb7bc98cb1742757f163df7"

PROFILE = {
    "action_type": ACTION_TYPE,
    "bridge_address": BRIDGE2_ADDRESS.lower(),
    "chain_id": ARBITRUM_CHAIN_ID,
    "fee_ceiling_bps": FEE_CEILING_BPS,
    "minimum_amount_atomic": MIN_AMOUNT_ATOMIC,
    "network_id": ARBITRUM_NETWORK_ID,
    "profile_id": PROFILE_ID,
    "profile_version": PROFILE_VERSION,
    "token_contract": NATIVE_USDC.lower(),
}
PROFILE_DIGEST = hashlib.sha256(json.dumps(
    PROFILE, ensure_ascii=True, separators=(",", ":"), sort_keys=True,
).encode("utf-8")).hexdigest()
