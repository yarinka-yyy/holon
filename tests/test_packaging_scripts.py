from pathlib import Path


def test_wallet_build_bundles_both_lending_profile_files() -> None:
    script = (
        Path(__file__).parents[1] / "packaging" / "build-wallet.ps1"
    ).read_text(encoding="utf-8")

    assert 'holon_lending\\read-profiles.json' in script
    assert '--add-data "$lendingReadProfile;holon_lending"' in script
    assert 'holon_lending\\action-profiles.json' in script
    assert '--add-data "$lendingActionProfile;holon_lending"' in script
    assert '--add-data "$networkAssets;holon_contracts"' in script


def test_wallet_build_embeds_the_holon_application_icon() -> None:
    script = (
        Path(__file__).parents[1] / "packaging" / "build-wallet.ps1"
    ).read_text(encoding="utf-8")

    assert '"assets\\holon.svg"' in script
    assert "build_icon.py" in script
    assert "--icon $iconPath" in script


def test_installer_build_is_single_production_pipeline() -> None:
    script = (
        Path(__file__).parents[1] / "packaging" / "build-installer.ps1"
    ).read_text(encoding="utf-8")

    assert 'pythonVersion -ne "3.13.14"' in script
    assert 'build-guard.ps1' in script
    assert 'build-wallet.ps1' in script
    assert 'build_package.py' in script
    assert 'generate_third_party_licenses.py' in script
    assert '--check' in script
    assert 'NOTICE_FILE' in script
    assert 'installer.iss' in script
    assert 'Holon-0.1.0-alpha-Setup.exe' in script
    assert 'winget' not in script.lower()


def test_m8_installer_defaults_to_explicit_extended_module_root() -> None:
    packaging = Path(__file__).parents[1] / "packaging"
    installer = (packaging / "build-installer.ps1").read_text(encoding="utf-8")
    assert '[string]$CompositionId = "extended"' in installer
    assert 'Join-Path $projectRoot "modules\\perpdex"' in installer
    assert '$compositionArguments += @("--module-root", $resolvedRoot)' in installer
    for name in ("build-guard.ps1", "build-wallet.ps1"):
        source = (packaging / name).read_text(encoding="utf-8")
        assert '$entry.module_id -ceq "holon.perpdex"' in source
        assert '$moduleBuildArguments += @("--collect-all", "hyperliquid")' in source
        assert source.index('if ($includeHyperliquidSdk)') > source.index(
            'if (-not $entry.enabled) { continue }'
        )


def test_guard_and_wallet_use_the_fixed_windows_version_resource() -> None:
    packaging = Path(__file__).parents[1] / "packaging"
    version = (packaging / "windows-version.txt").read_text(encoding="utf-8")
    assert "filevers=(0, 1, 0, 0)" in version
    assert "prodvers=(0, 1, 0, 0)" in version
    assert "StringStruct('ProductVersion', '0.1.0-alpha')" in version
    for name in ("build-guard.ps1", "build-wallet.ps1"):
        script = (packaging / name).read_text(encoding="utf-8")
        assert 'windows-version.txt' in script
        assert '--version-file $versionFile' in script
