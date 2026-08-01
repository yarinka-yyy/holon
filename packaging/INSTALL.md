# Holon for Windows

## Release availability

No public installer or GitHub Release is available yet. The first approved alpha
asset will be named `Holon-0.1.0-alpha-Setup.exe`. A locally built unsigned file
is a development artifact, not a public release.

## End-user installation

The approved Setup requires:

- Windows 11 x64;
- an existing Hermes Desktop installation in the range `>=0.18.2,<0.19.0`;
- Hermes closed before installation.

Run the Setup as the current user. It does not require administrator privileges,
Python, Node.js, compilers, or other developer tools. The wizard detects one
compatible Hermes installation, installs Wallet, Guard, the Hermes plugin, both
Holon skills, and shared package assets, then enables only the Holon plugin after
explicit confirmation. It does not grant the plugin permission to override tools.

After a successful install, open Hermes normally and type `/holon`.

Program files are installed under `%LOCALAPPDATA%\Holon\app`. Encrypted Wallet
data, settings, and secret-free journals are kept separately under
`%LOCALAPPDATA%\Holon\data`. Reinstall, upgrade, and ordinary uninstall preserve
that data by default. Permanent data deletion is a separate twice-confirmed choice.

If Setup cannot find a compatible Hermes installation or cannot safely close the
selected Hermes processes, it changes no files. Close Hermes manually, verify its
version, and run Setup again. Do not paste Wallet secrets or diagnostic content
into chat while troubleshooting.

## Developer build

Source builds require CPython 3.13.14, `uv`, and the official Inno Setup compiler
(`ISCC.exe`). From the repository root:

```powershell
uv sync --locked --all-groups
.\.venv\Scripts\python.exe -m pytest
.\packaging\build-installer.ps1
```

The build script requires the project `.venv` to report exactly CPython 3.13.14,
rebuilds native Guard and Wallet executables, validates production staging, and
writes an unsigned local artifact to `dist\Holon-0.1.0-alpha-Setup.exe`. It
recreates generated `build/` and `dist/` directories as needed.

The PowerShell install and uninstall scripts are transactional internal backends.
They are invoked by Setup; end users should not run them directly.
