"""Integrity-pinned Hyperliquid V1 product profile."""

from __future__ import annotations

import hashlib
import json

PROFILE_ID = "hyperliquid-mainnet-v1"
PROFILE_VERSION = "1"
API_URL = "https://api.hyperliquid.xyz"
HLP_ADDRESS = "0xdfc24b077bc1425ad1dea75bcb6f8158e10df303"
HLP_NAME = "Hyperliquidity Provider (HLP)"
REFERRAL_CODE = "YARINKA"
SUPPORTED_MARKETS = ("BTC", "ETH", "SOL")
ACTION_TYPES = (
    "CLOSE_POSITION", "HLP_DEPOSIT", "HLP_WITHDRAW", "OPEN_POSITION",
)
MAX_OPEN_NOTIONAL_USDC = "100"
MAX_HLP_DEPOSIT_USDC = "100"
MAX_SLIPPAGE_PERCENT = "1"
MIN_LEVERAGE = 1
MAX_LEVERAGE = 3
DEFAULT_LEVERAGE = 2
MARKET_REVIEW_SECONDS = 90
HLP_REVIEW_SECONDS = 300
HLP_LOCKUP_DAYS = 4
REFERRAL_DISCLOSURE = (
    "Referral assignment: this entry will first assign Hyperliquid referral "
    "code YARINKA. The code owner may receive referral rewards. Holon adds no "
    "fee. Cancel to stop the entire operation."
)

PROFILE = {
    "action_types": list(ACTION_TYPES),
    "api_url": API_URL,
    "default_leverage": DEFAULT_LEVERAGE,
    "hlp_address": HLP_ADDRESS,
    "hlp_lockup_days": HLP_LOCKUP_DAYS,
    "hlp_name": HLP_NAME,
    "max_hlp_deposit_usdc": MAX_HLP_DEPOSIT_USDC,
    "max_leverage": MAX_LEVERAGE,
    "max_open_notional_usdc": MAX_OPEN_NOTIONAL_USDC,
    "max_slippage_percent": MAX_SLIPPAGE_PERCENT,
    "min_leverage": MIN_LEVERAGE,
    "profile_id": PROFILE_ID,
    "profile_version": PROFILE_VERSION,
    "referral_code": REFERRAL_CODE,
    "supported_markets": list(SUPPORTED_MARKETS),
}
PROFILE_DIGEST = hashlib.sha256(
    (json.dumps(PROFILE, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n").encode("utf-8")
).hexdigest()
