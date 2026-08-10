from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_manifest_covered_modules_are_byte_exact_on_windows_ci() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "windows-packaged-wallet-e2e.yml").read_text(
        encoding="utf-8"
    )
    assert "modules/** -text" in attributes
    assert "module-manifest.json text eol=lf" in attributes
    assert "- '.gitattributes'" in workflow
    assert "Verify canonical module manifest checkout" in workflow
    assert "git checkout -- modules/perpdex" in workflow
