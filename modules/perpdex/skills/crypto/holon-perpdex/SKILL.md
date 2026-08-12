---
name: perpdex-holon
description: "Read and use the bounded Hyperliquid PerpDEX module when it is installed."
version: "0.2.0-alpha"
author: Holon
license: Apache-2.0
platforms: [windows]
metadata:
  hermes:
    tags: [holon, hyperliquid, perpdex, hlp]
    related_skills: [holon, earn-holon]
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
5. Call `holon_perpdex_fund_prepare` only with that exact user amount. It internally performs the
   one-use preview and opens local Wallet Review itself. Do not show a technical preview, request
   another chat confirmation, or call an execute tool. The original exact funding request authorizes
   Review only; it does not authorize signing or broadcast.
6. Do not repeat route/address/contract data in chat: Wallet Review shows the amount, Arbitrum
   network, native-USDC route and maximum fee in a human-readable summary, with technical details
   available on demand. Never retry, substitute an action, or begin a trade after funding.
7. For `OPEN_POSITION`, ask only for missing market, direction, leverage, or amount. An ordinary
   amount such as "open ETH long 2x with 6 USDC" means **6 USDC margin**: pass it only as
   `amount_usdc` and let the protected tool bind the final notional to margin × leverage. Treat
   `notional`, `total position`, or `including leverage` as the final position notional and pass it
   only as `notional_usdc`. Do not normally send both representations; if the user explicitly gives
   both, they must exactly match leverage or the request is refused. Use `ISOLATED` when the user did
   not state a margin mode and preserve an explicit `CROSS` or `ISOLATED` choice. The final rounded
   order value must be at least `10 USDC`; if it is smaller, explain the minimum and an approximate
   required margin at the requested leverage instead of trying to open Wallet Review.
8. For `CLOSE_POSITION`, ask only for the market or whether to close all/a percentage when those
   facts are missing. A complete open or close request is already sufficient to open a local Wallet
   Review: call `holon_perpdex_prepare` once. It internally consumes its one-use preview and opens
   Review. Do not show a technical preview, request a separate chat confirmation, or call an execute
   tool. This authorizes Review only, never signing or external submission.
9. HLP deposit and withdrawal keep the separate chat-confirmation flow. Explain price/slippage,
   size, margin mode, leverage/reduce-only state, HLP lock-up, checks, caveats, and referral
   disclosure when present, then wait for explicit confirmation in a later user message before
   calling `holon_perpdex_execute` once with that preview's exact `preview_digest`.
10. After either execute returns `AWAITING_LOCAL_CONFIRMATION`, end the turn. Wallet Review and a
   fresh local password are required; Hermes is never signing authority and must never ask for a
   password, seed phrase, private key, or signed payload.
11. Never promise profit, call HLP safe, treat APR as APY, or represent `NOT_ASSESSED` as a risk
   rating. Never retry a failed, partial, timed-out, or uncertain write automatically.
12. `CLOSE_POSITION` and `HLP_WITHDRAW` never require referral assignment. Do not present
    referral text on ordinary reads; show it only when the prepared entry preview contains it.
