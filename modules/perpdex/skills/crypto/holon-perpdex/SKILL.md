---
name: holon-perpdex
description: "Read and use the bounded Hyperliquid PerpDEX module when it is installed."
version: "0.1.0-alpha"
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
3. Never choose a market, direction, leverage, position size, close percentage, or vault
   amount for the user. Ask for every missing choice instead of filling it in.
4. Call `holon_perpdex_prepare` only with the user's exact parameters. For an open position,
   the user must choose `ISOLATED` or `CROSS` and an integer leverage supported by current
   Hyperliquid metadata. Explain the returned account, action, price/slippage, size,
   margin mode, leverage/reduce-only state, HLP lock-up, checks, caveats, and referral
   disclosure when present. A preview is not an executed action.
5. End the turn and wait for an explicit confirmation in a later user message. Only then
   call `holon_perpdex_execute` once with that preview's exact `preview_digest`. Never call
   execute in the same turn as prepare and never substitute or reconstruct a digest.
6. After execute returns `AWAITING_LOCAL_CONFIRMATION`, end the turn. Wallet Review and a
   fresh local password are required; Hermes is never signing
   authority and must never ask for a password, seed phrase, private key, or signed payload.
7. Never promise profit, call HLP safe, treat APR as APY, or represent `NOT_ASSESSED` as a
   risk rating. Never retry a failed, partial, timed-out, or uncertain write automatically.
8. `CLOSE_POSITION` and `HLP_WITHDRAW` never require referral assignment. Do not present
   referral text on ordinary reads; show it only when the prepared entry preview contains it.
