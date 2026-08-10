from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_module_manifests_are_pinned_to_lf_and_trigger_windows_ci() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    workflow = (ROOT / ".github" / "workflows" / "windows-packaged-wallet-e2e.yml").read_text(
        encoding="utf-8"
    )
    assert "module-manifest.json text eol=lf" in attributes
    assert "- '.gitattributes'" in workflow
    assert "Verify canonical module manifest checkout" in workflow
