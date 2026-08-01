# Holon

Holon is a Windows crypto layer for Hermes that keeps Wallet secrets and signing authority outside the chat model. Hermes interprets intent and presents safe public results; a separate local Wallet handles secret input, exact review, confirmation, signing, and broadcast.

> **Alpha status.** Holon is pre-release software for controlled personal use and technical review. It is not a production consumer wallet. No public installer, GitHub Release, signed binary, or checksum is available yet. Release assets require separate final verification and approval.

## What it does

- Integrates with compatible Hermes Desktop (`>=0.18.2,<0.19.0`) through a standard plugin.
- Provides a separate local Wallet for profile creation/import, public balances, local history, approvals, and supported ETH/USDC flows on Ethereum Mainnet and Base.
- Enforces exact-action review, fresh local Wallet-password authentication, one active protected flow, replay protection, and one broadcast attempt.
- Compares public Base native-USDC rates and positions for Aave V3, Compound III, and the selected Morpho V1 vault without unlocking the Wallet.
- Supports bounded Base-USDC supply and withdrawal only for those integrity-pinned profiles; protocol selection, approval, and every signable phase remain explicit local Wallet actions.

## Security model

Chat confirmation is not a signing decision in Holon.

- Seed phrases, private keys, Wallet passwords, decrypted vault content, and raw signed bytes stay inside the local Wallet. They must never be copied into Hermes, logs, diagnostics, or public screenshots.
- Hermes receives only public data, safe status/error/refusal information, and public transaction identifiers.
- The local Guard validates protected-flow state, compatibility, integrity, and policy. Missing or uncertain authority state fails closed while ordinary Hermes work remains available.
- Any material change, expiry, replay, interruption, or uncertain broadcast result invalidates authority. Financial actions are never automatically retried.

See [the architecture overview](docs/ARCHITECTURE.md) for component and data-flow detail.

## Install and run

The end-user Setup is not public yet. When an approved alpha Setup is available, it installs per Windows user, detects a compatible Hermes installation, and requires no administrator privileges or developer tooling. Detailed prerequisites, install, uninstall, and source-build steps are in [packaging/INSTALL.md](packaging/INSTALL.md).

## Repository layout

| Path | Purpose |
| --- | --- |
| `src/` | Wallet, Guard, Hermes plugin, shared contracts, and Lending code |
| `skills/` | Hermes instructions for the `holon` and `holon-lending` capabilities |
| `tests/` | Deterministic and component-level verification |
| `packaging/` | Windows installer and verified package-build support |
| `docs/` | Public architecture material |

Generated `build/`, `dist/`, virtual environments, Wallet data, diagnostics, and release archives are ignored by Git and are not source artifacts.

## Supported scope and limits

- Windows 11 and one compatible Hermes installation are the supported environment.
- Public Wallet reads cover Ethereum Mainnet and Base; the required token allowlist is native USDC on those networks.
- Lending is Base Mainnet native USDC only: Aave V3, Compound III, and one selected Morpho V1 vault. Rates can be `LIVE`, `STALE`, or `UNAVAILABLE`; unavailable data is never shown as zero or fabricated.
- Holon is not a general wallet, portfolio indexer, hardware-wallet client, arbitrary smart-contract caller, borrowing tool, or autonomous trading/rebalancing system.
- MVP1 does not claim protection from a compromised operating system, hostile same-user software, deliberate secret sharing in chat, or unsigned-release supply-chain compromise.

## Public review safety

- Never record or publish a Wallet screen that contains recovery material, a private key, a Wallet password, decrypted vault content, or raw signed bytes.
- Keep `LIVE`, `CACHED`, `STALE`, `UNAVAILABLE`, pending, and unknown results distinct. Missing data is not a zero balance, and an uncertain transaction is not confirmed.
- Any real-fund demonstration requires a separate human-approved low-value flow with current checks and exact local Wallet confirmation.
- Never retry a refused, failed, interrupted, expired, or uncertain financial action automatically.

## Development verification

The source repository targets CPython 3.13.14 and uses `uv` for the locked development environment:

```powershell
git clone https://github.com/yarinka-yyy/holon.git
cd holon
uv sync --locked --all-groups
.\.venv\Scripts\python.exe -m pytest
```

Building a local unsigned Setup additionally requires the official Inno Setup compiler. This is a source-build procedure, not an end-user installation path; see [packaging/INSTALL.md](packaging/INSTALL.md#developer-build).

## License

Holon is licensed under the [Apache License 2.0](LICENSE).
