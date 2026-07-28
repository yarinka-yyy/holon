---
name: holon
description: "Use for any Holon, wallet, crypto, transfer, or lending request. Covers setup, balances, approvals, action status, cancellation, and recovery; load before generic wallet guidance."
version: "0.1.0-alpha"
author: Holon
license: Apache-2.0
platforms: [windows]
metadata:
  hermes:
    tags: [holon, wallet, crypto, guard, onboarding]
    related_skills: [holon-lending]
---

# Holon

## Non-negotiable rules

1. **Always reply in the language of the user's latest meaningful message.** If the user changes language, change with them. Keep tool names, protocol names, field names, addresses, and error codes exact. Never answer in English merely because this skill is written in English. If a brand-new conversation contains only `/holon` and gives no language signal, ask one short question for the preferred language and do nothing else until the user answers.
2. **The model is never signing or policy authority.** Chat confirmation does not unlock Wallet, approve a transaction, sign, or broadcast.
3. **Secrets stay inside Wallet.** Never ask the user to type or paste a seed phrase, private key, Wallet password, recovery material, raw signed bytes, or secret-bearing screenshot into chat or tool arguments.
4. **Fail closed.** Do not invent unsupported routes, arbitrary calldata, contract calls, approvals, retries, or recovery steps. Preserve the meaning of Holon error and refusal codes.

## What Holon is

Holon is a crypto layer integrated with Hermes through narrow `holon_*` tools:

- Hermes interprets intent, requests public data, explains results, and coordinates supported actions.
- Guard owns protected-flow state, policy enforcement, compatibility checks, and recovery coordination.
- The separate Holon Wallet is the only place for account creation/import, secrets, fresh password entry, exact review, confirmation, signing, and broadcast.

Opening Wallet never grants reusable signing authority. Each critical action requires its own local review and fresh Wallet authentication.

## Route the request

Use the smallest matching path:

| User intent | Action |
|---|---|
| Start, set up, create, import, or open Holon | Follow **Onboarding and Wallet access**. |
| Check integration availability | Call `holon_health`. |
| Read Wallet ETH or USDC balances | Call `holon_wallet_balances`. |
| Send ETH or USDC | Follow **Transfer workflow**. |
| Compare yield, read positions, supply, withdraw, approval, or Lending recovery | Load and follow `holon-lending`. |
| Check, cancel, or recover an existing action | Follow **Lifecycle and recovery** using the exact known `action_id`. |

If the request is outside Holon's supported scope, say so plainly. Do not redirect it through terminal, browser automation, another wallet, raw RPC, or user-supplied calldata as a workaround.

## Onboarding and Wallet access

For a bare `/holon` after the user's language is known:

1. Call `holon_health` once.
2. If Holon is reachable and no protected or recovery flow is active, call `holon_open_wallet` to open or activate Wallet. A `SIGNING_DISABLED` result may still allow public Wallet setup; explain that protected actions remain unavailable.
3. Tell the user to create or import the account only in the Wallet window. Never ask which secret they chose and never ask them to copy recovery material back into chat.
4. Ask the user to return when the local Wallet step is complete. Do not poll automatically.

If health is unavailable, incompatible, unknown, or ambiguous, explain that protected Wallet actions are unavailable and stop. If a protected or recovery flow is active, do not start onboarding or another action; route to lifecycle handling instead.

If `/holon` includes a meaningful instruction, handle that instruction directly rather than forcing the generic onboarding sequence.

## Public Wallet reads

Use `holon_wallet_balances` for the active public Account on Ethereum and Base. Public reads do not require a Wallet password and do not create signing authority.

Report unavailable, stale, cached, or degraded data exactly as returned. Never turn missing data into a zero balance and never claim a refresh succeeded when it did not.

## Transfer workflow

Holon Transfer supports exact ETH or allowlisted USDC transfers on Ethereum or Base, subject to active policy and trusted-recipient rules.

1. Obtain all four material fields from the user: `network`, `asset`, `amount`, and `recipient`.
2. Ask a concise clarification for every missing or ambiguous field. Never infer a network from the token name, choose a recipient, round the amount, or alter an address.
3. Use `holon_wallet_balances` first only when the request depends on available funds or the user asks to inspect them.
4. Call `holon_prepare_transfer` once with the exact semantic request.
5. Explain the returned summary, refusal, or error without contradicting its code.

When the result is `PROTECTED_FLOW_STARTED`, tell the user that the exact review and any password entry occur in Holon Wallet, include the safe `action_id`, and end the turn. Do not call another ordinary tool, poll, retry, or submit a second prepare request.

After the user returns, use `holon_transfer_status` for that exact transfer. Use `holon_cancel_transfer` only after an explicit cancel request and `holon_recover_transfer` only when recovery is actually required. A failed, refused, stale, expired, cancelled, or uncertain action is never retried automatically; a new action needs a new explicit user request.

## Lifecycle and recovery

Use only an exact `action_id` returned by Holon or already present in the conversation. Never fabricate or substitute one.

- Transfer-specific lifecycle: `holon_transfer_status`, `holon_cancel_transfer`, `holon_recover_transfer`.
- Common protected-action lifecycle: `holon_action_status`, `holon_cancel_action`, `holon_recover_action`.

Status is read-only. Cancellation never authorizes signing. Recovery does not reuse an old signature, signed payload, nonce, or approval. If Holon reports that Wallet-only intervention is required, direct the user to Wallet and stop.

## Completion standard

A response is complete only when it:

- uses the user's language;
- distinguishes a public read, preview, protected request, local approval, broadcast, and confirmed receipt;
- states the next human action clearly;
- retains safe identifiers and source error/refusal codes;
- makes no promise that an unsigned, unbroadcast, pending, cached, or unavailable result is complete.
