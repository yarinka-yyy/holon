# Holon for Windows

## Release availability

The public alpha is available from the
[v0.1.0-alpha GitHub Release](https://github.com/yarinka-yyy/holon/releases/tag/v0.1.0-alpha).
Download `Holon-0.1.0-alpha-Setup.exe` and `SHA256SUMS.txt` from that page.
The Setup is unsigned, so Windows may display an unknown-publisher warning.
A file built locally with the same name is still a development artifact, not
the published release.

Verify the downloaded Setup before running it:

```powershell
Get-FileHash .\Holon-0.1.0-alpha-Setup.exe -Algorithm SHA256
```

Compare the displayed hash with the matching line in `SHA256SUMS.txt`.
Checksums detect corruption or a different file. They do not provide publisher
authentication or replace code signing.

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

## Third-party licenses and Qt rebuilds

Holon source code remains under Apache License 2.0. The Windows package also
contains third-party software listed in `NOTICE` and
`THIRD_PARTY_LICENSES.txt`. These files are installed under
`%LOCALAPPDATA%\Holon\app\licenses`.

Holon Wallet uses the Qt 6.11.1 libraries supplied by PySide6 and Shiboken6
6.11.1 under LGPLv3. The release page includes the exact source archives for
the Qt modules bundled in Wallet and for PySide6/Shiboken6. Ordinary users do
not need those archives to install or run Holon.

To rebuild Holon with a compatible modified Qt/PySide6 build:

1. Check out the `v0.1.0-alpha` source tag.
2. Build Qt and PySide6 from the matching release source archives using their
   upstream build instructions.
3. Create the locked Holon environment with `uv sync --locked --all-groups`.
4. Replace only the pinned `shiboken6` and `pyside6-essentials` wheels in that
   environment with the compatible rebuilt wheels:

```powershell
uv pip install --python .\.venv\Scripts\python.exe --no-deps `
    C:\path\to\shiboken6-6.11.1-*.whl `
    C:\path\to\pyside6_essentials-6.11.1-*.whl
.\packaging\build-installer.ps1
```

The resulting Setup is a separate local build. It must not be represented as
the official Holon release unless it is published by the project owner.
