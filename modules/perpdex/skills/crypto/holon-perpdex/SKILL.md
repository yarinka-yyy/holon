---
name: holon-perpdex
description: "Read and use the bounded Hyperliquid PerpDEX module when it is installed."
version: "0.2.0-alpha"
author: Holon
license: Apache-2.0
platforms: [windows]
metadata:
  hermes:
    tags: [holon, hyperliquid, perpdex, hlp]
    related_skills: [holon, holon-earn]
---

# Holon PerpDEX

1. Always reply in the language of the user's latest meaningful message.
2. Use `holon_perpdex_markets` and `holon_perpdex_portfolio` only for current public data.
   If a public read is unavailable, stale, or fails, say that data is unavailable; never call
   it zero and do not prepare a trade or funding action from it.
3. When the user asks to trade, read the portfolio first. If `withdrawable_usdc` is zero,
   say that there is no trading collateral and ask for one exact native-USDC funding amount.
   Do not show markets first and do not choose an amount. Phrases such as "all" or "everything"
   are not an amount and require clarification.
4. Funding supports only an exact decimal-string amount with at most six fractional digits and
   at least `5` USDC. It is one native USDC transfer on Arbitrum One to the active account's
   official Hyperliquid Bridge2 route. Do not offer `USDC.e`, another asset or network, a
   third-party bridge, another account, `usdClassTransfer`, auto-trading, or retry.
5. Call `holon_perpdex_fund_prepare` only with that exact user amount. Explain the returned
   amount, Arbitrum network, native-USDC contract, Bridge2 address, maximum gas fee, minimum
   credit, irreversibility, and that broadcast is only `PENDING_CREDIT` until a later public
   portfolio read. A funding preview is not a trade preview or an executed action.
6. End the turn and wait for explicit confirmation in a later user message. Only then call
   `holon_perpdex_fund_execute` once with that preview's exact `preview_digest`. Never execute
   in the same turn as prepare, substitute a digest, or begin a trade after funding.
7. Call `holon_perpdex_prepare` only with the user's exact trade or HLP parameters. For an open
   position, the user must choose `ISOLATED` or `CROSS` and a supported integer leverage.
   Explain price/slippage, size, margin mode, leverage/reduce-only state, HLP lock-up, checks,
   caveats, and referral disclosure when present. A preview is not an executed action.
8. End the turn and wait for explicit confirmation in a later user message. Only then call
   `holon_perpdex_execute` once with that preview's exact `preview_digest`.
9. After either execute returns `AWAITING_LOCAL_CONFIRMATION`, end the turn. Wallet Review and a
   fresh local password are required; Hermes is never signing authority and must never ask for a
   password, seed phrase, private key, or signed payload.
10. Never promise profit, call HLP safe, treat APR as APY, or represent `NOT_ASSESSED` as a risk
    rating. Never retry a failed, partial, timed-out, or uncertain write automatically.
11. `CLOSE_POSITION` and `HLP_WITHDRAW` never require referral assignment. Do not present
    referral text on ordinary reads; show it only when the prepared entry preview contains it.
