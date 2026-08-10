from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_module_manifests_are_pinned_to_lf_for_windows_checkout() -> None:
    attributes = (ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "module-manifest.json text eol=lf" in attributes
