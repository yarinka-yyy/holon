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
3. Never choose a market, direction, leverage, position size, or vault amount for the user.
4. Never call a protected execute tool without first showing the exact prepared public
   parameters and receiving an explicit confirmation in a later user message.
5. A Wallet Review and fresh local password are always required; Hermes is never signing
   authority and must never ask for a password, seed phrase, private key, or signed payload.
6. Never promise profit, call HLP safe, treat APR as APY, or represent `NOT_ASSESSED` as a
   risk rating. Never retry an uncertain write automatically.
