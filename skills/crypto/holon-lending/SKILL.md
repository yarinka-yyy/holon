---
name: holon-lending
description: "Use for any Holon Lending, Aave, Compound, Morpho, or Base USDC request. Covers yield, positions, earnings, supply, withdraw, approval, status, cancellation, and recovery."
version: "0.1.0-alpha"
author: Holon
license: Apache-2.0
platforms: [windows]
metadata:
  hermes:
    tags: [holon, lending, usdc, aave, compound, morpho]
    related_skills: [holon, holon-earn]
---

# Holon Lending

## Non-negotiable rules

1. **Always reply in the language of the user's latest meaningful message.** If the user changes language, change with them. Keep tool names, protocol names, field names, addresses, and error codes exact. Never answer in English merely because this skill is written in English. If a brand-new conversation contains only `/holon-lending` and gives no language signal, ask one short question for the preferred language and do nothing else until the user answers.
2. **Lending chat is not authority.** Protocol choice or confirmation in chat only permits Hermes to request a protected flow. Password entry, exact review, confirmation, signing, and broadcast remain inside Holon Wallet.
3. **Never handle secrets or raw transactions.** Do not ask for Wallet passwords, seed phrases, private keys, raw calldata, signed bytes, or arbitrary contract parameters.
4. **Never guess or retry a financial action.** Missing protocol, action, amount mode, or amount must be clarified. Failed, refused, stale, cancelled, expired, or uncertain actions require a new explicit user decision.

## Supported scope

MVP1 supports only native USDC on Base for these integrity-pinned profiles:

- Aave V3;
- Compound III;
- the selected Morpho V1 Vault.

Morpho V2, other vaults or pools, other assets or networks, arbitrary contract addresses, user-supplied selectors, referral codes, and calldata are unsupported. Do not propose them as Holon actions.

## Choose the read path

- Use `holon_lending_compare` to compare verified supported markets and recommend by returned `confirmed_total` data.
- Use `holon_lending_positions` for current protocol positions.
- Use `holon_lending_portfolio` for combined positions, tracked earnings, current confirmed yield, and optional local history.

Explain freshness and availability. Distinguish `base_yield`, incentives or bonuses, and `confirmed_total`. Unknown incentives stay unknown. A displayed rate is current public data, not a guarantee of future earnings.

Read-only failures do not justify a write action. Never substitute a cached or unavailable value as live without saying so.

## Resolve an action

A Lending write request needs four exact semantic fields:

1. `protocol`: the explicitly chosen Aave V3, Compound III, or Morpho V1 profile;
2. `action`: `supply` or `withdraw`;
3. `amount_mode`: `exact` or `all`;
4. `amount`: a decimal string for `exact`, or `null` for `all`.

Do not infer an amount, convert `exact` to `all`, or treat approximate language as exact.

If the user did not name a protocol:

1. call `holon_lending_compare`;
2. explain the recommendation and material caveats in the user's language;
3. ask the user to confirm one protocol explicitly;
4. stop without previewing or executing until they answer.

A request such as “use the best protocol” authorizes comparison, not automatic protocol selection or execution.

## Preview versus execute

Use `holon_lending_prepare` only when the user asks for a preview, review, estimate, simulation, or wants to inspect the proposed action before starting Wallet authority. A preview is non-executable and does not carry forward an approval, nonce, fee, or prepared transaction.

Use `holon_lending_execute` when protocol, action, amount mode, and amount are all explicit and the user has asked to perform the action. A separate preview is not mandatory and must not become an extra hidden requirement.

When execution returns `PROTECTED_FLOW_STARTED`, state that the exact action must be reviewed in Holon Wallet, include the safe `action_id`, and end the turn. Do not poll, call execute again, or invoke unrelated tools while the protected flow is active.

## Composite Supply and approval

Both exact Supply and Supply all are supported for Aave V3, Compound III, and Morpho V1.
For an explicit Supply all request, call `holon_lending_execute` with
`amount_mode=all` and `amount=null`; Wallet freezes the current live USDC amount
before its local Review. Any required approval remains exact to that frozen amount.
Never replace the user's `all` request with `exact` or claim that Supply all is
unsupported because of an old validator result.

Supply may require an exact USDC approval before protocol supply:

1. Holon Wallet reviews and authenticates the approval as its own signable phase.
2. Only a confirmed and validated approval receipt permits a fresh Supply phase.
3. Wallet may open the new Supply Review automatically, but Supply still has a new action identity, fresh preflight, fresh password, and separate explicit Wallet confirmation.
4. Hermes must not call `holon_lending_execute` again to advance from approval to Supply.

Approval is never generic. Do not request unlimited allowance, invent an approval target, or present approval as completed Supply.

Revoke is available only through the recovery path offered by Holon for the exact operation. It sets the pinned protocol allowance to zero and requires its own local Review and authentication. Never synthesize a standalone approval or revoke through another tool.

## Withdraw

Both exact withdraw and withdraw all are supported for pinned profiles. For exact withdraw, preserve the user's decimal amount. For withdraw all, use `amount_mode=all` and `amount=null`; do not replace it with an estimated position amount.

Insufficient position, debt, liquidity, paused protocol state, changed profile identity, stale data, simulation failure, or expiry must remain a refusal or failure. Do not route around it.

## Status, cancellation, and recovery

After the user returns from Wallet, call `holon_action_status` with the exact known `action_id`.

- Use `holon_cancel_action` only after the user explicitly asks to cancel and Holon permits cancellation.
- A Wallet-local Lending `resume_or_revoke` state is not generic Guard recovery. When the user asks to continue that current Lending/Morpho recovery, call `holon_open_wallet`, including while Guard reports `RECOVERY_REQUIRED`; do not call `holon_recover_action` for it.
- `ACTIVATED` means Wallet activation was requested and `OPENED` means its launch was verified. Do not claim that the window is visibly open; ask the user to use the local Recovery screen only after they can see it.
- Use `holon_recover_action` only when the exact known action is reported as generic recovery-required. If Wallet-local recovery offers Resume, Revoke, or Cancel, explain those exact choices and wait for the user. Do not choose one automatically.
- Never reuse an old action, preview, signature, signed bytes, nonce, fee fields, or transaction after failure or uncertainty.

Preserve whether the reported outcome is local approval, broadcast, pending receipt, confirmed receipt, failed receipt, refusal, cancellation, expiry, or recovery-required. Only a verified confirmed receipt proves the on-chain phase completed.

## Completion standard

A Lending response is complete only when it:

- uses the user's language;
- names the exact supported protocol and Base USDC scope;
- distinguishes rates, positions, preview, protected execution, approval, Supply/Withdraw, and receipt status;
- gives one clear next step without implying chat-based signing;
- preserves Holon codes and safe identifiers without exposing or requesting secret material.
