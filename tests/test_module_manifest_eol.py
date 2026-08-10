from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_integrity_pinned_inputs_are_byte_exact_on_windows_ci() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "windows-packaged-wallet-e2e.yml").read_text(
        encoding="utf-8"
    )
    assert "modules/** -text" in attributes
    assert "src/holon_wallet/qml/assets/** -text" in attributes
    assert "src/holon_modules/module-catalog.json text eol=lf" in attributes
    assert "module-manifest.json text eol=lf" in attributes
    assert "- '.gitattributes'" in workflow
    assert "Verify byte-exact integrity inputs" in workflow
    assert "src/holon_modules/module-catalog.json" in workflow
    assert "tests/test_network_asset_registry.py::test_official_icon_files_match_pinned_sha256" in workflow


def test_windows_package_build_uses_the_locked_virtual_environment() -> None:
    workflow = (ROOT / ".github" / "workflows" / "windows-packaged-wallet-e2e.yml").read_text(
        encoding="utf-8"
    )
    assert "Resolve-Path -LiteralPath '.venv\\Scripts\\python.exe'" in workflow
