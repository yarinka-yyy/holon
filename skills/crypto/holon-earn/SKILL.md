---
name: holon-earn
description: "Use for any Holon Earn, yield, APY, return, Lending-versus-Vault, provider freshness, exit-condition, or risk-state request."
version: "0.2.0-alpha"
author: Holon
license: Apache-2.0
platforms: [windows]
metadata:
  hermes:
    tags: [holon, earn, yield, lending, vaults]
    related_skills: [holon, holon-lending]
---

# Holon Earn

## Non-negotiable rules

1. **Always reply in the language of the user's latest meaningful message.** Keep tool names, provider names, metric kinds, and status codes exact. If a brand-new conversation contains only `/holon-earn` and gives no language signal, ask one short question for the preferred language and do nothing else until the user answers.
2. **Earn reads are not financial authority.** `holon_earn_portfolio` reads normalized public data. It cannot approve, sign, broadcast, allocate, rebalance, or move funds.
3. **Never handle secrets.** Do not ask for Wallet passwords, seed phrases, private keys, exchange credentials, API secrets, raw transactions, or signed bytes.
4. **Never invent missing data.** Preserve `LIVE`, `STALE`, `CACHED`, `UNAVAILABLE`, provider degradation, and total completeness exactly as returned.

## Read and compare

Use `holon_earn_portfolio` for a cross-provider Earn overview. It returns only providers present in the installed composition. An absent provider is not an error; an installed provider with no fresh or cached position makes the total explicitly incomplete.

Keep these metric meanings separate:

- `SUPPLY_APY` is a current annualized supply metric and has no historical period.
- `TRAILING_RETURN` is a realized historical return and must name its period.

Never compare unlike metrics as if they measured the same thing. State the metric kind, period when present, freshness, and whether the position is live or cached. A current rate or historical return is not a promise of future income.

## Risk and exit conditions

M7 has no approved Holon risk methodology. Every normalized result is `NOT_ASSESSED`, with no band and no factors. Say plainly that this means “not assessed,” not “safe,” “low risk,” or “unknown score.” Do not create a score, rank, band, recommendation, or synthetic risk factor.

Report exit conditions separately from risk. Preserve lockups, notice periods, liquidity limitations, fees, and unavailable conditions as returned. Do not infer instant exit from the absence of a displayed lockup.

## Route Lending actions

Earn is an overview. If the user asks to supply, withdraw, approve, compare only the supported Base USDC Lending protocols, or handle a Lending action lifecycle, load and follow `holon-lending`.

Vault actions are unavailable unless an installed optional module exposes its own reviewed capability and skill. Never route a Vault action through Lending tools or synthesize a generic module write request.

## Completion standard

An Earn response is complete only when it:

- uses the user's language;
- names the provider, protocol, network, category, and metric kind;
- states freshness and whether totals are complete;
- preserves exit constraints separately from the fixed `NOT_ASSESSED` risk state;
- gives one supported next step without implying allocation or signing authority.
