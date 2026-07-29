# Holon for Windows

Holon is installed for the current Windows user with
`Holon-0.1.0-alpha-Setup.exe`. The wizard requires an existing compatible
Hermes installation, installs Wallet, Guard, the Hermes plugin, and both Holon
skills, and enables the plugin only after explicit confirmation on Install.

Close Hermes before installation. On the Finish page, open Hermes and type
`/holon`. Uninstall preserves `%LOCALAPPDATA%\Holon\data` by default. Permanent
Wallet-data deletion is a separate, twice-confirmed option.

## Developer build

Run `packaging\build-installer.ps1` with the project CPython 3.13.14 environment
and the official Inno Setup compiler installed. The script rebuilds both native
executables, creates verified production staging, and writes the unsigned alpha
installer to `dist\Holon-0.1.0-alpha-Setup.exe`.

The PowerShell install and uninstall scripts are internal transactional backends;
end users do not need to run commands or install Python, Inno Setup, or other
developer tooling.
