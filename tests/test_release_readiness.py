from __future__ import annotations

import hashlib
import io
import importlib.util
import json
from pathlib import Path
import re
import ssl
from urllib.parse import unquote

import pytest


ROOT = Path(__file__).parents[1]
PACKAGING = ROOT / "packaging"
EXPECTED_SOURCES = {
    "qtbase-everywhere-src-6.11.1.tar.xz": 50648500,
    "qtdeclarative-everywhere-src-6.11.1.tar.xz": 38744644,
    "qtsvg-everywhere-src-6.11.1.tar.xz": 2336944,
    "qtimageformats-everywhere-src-6.11.1.tar.xz": 2032792,
    "qtquicktimeline-everywhere-src-6.11.1.tar.xz": 97268,
    "pyside-setup-everywhere-src-6.11.1.tar.xz": 17963432,
}


def _load(name: str):
    path = PACKAGING / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_third_party_inventory_is_versioned_and_bundle_is_current() -> None:
    value = json.loads((PACKAGING / "third-party-components.json").read_text(encoding="utf-8"))
    assert value["schema_version"] == 1
    assert value["python"] == {"license": "PSF-2.0", "version": "3.13.14"}
    names = [item["name"] for item in value["components"]]
    assert len(names) == len(set(names))
    assert {"pyside6-essentials", "shiboken6", "pyinstaller", "x25519"} <= set(names)
    assert all(item["license"].casefold() not in {"unknown", "proprietary"} for item in value["components"])
    generator = _load("generate_third_party_licenses")
    assert (ROOT / "THIRD_PARTY_LICENSES.txt").read_text(encoding="utf-8") == generator.render()


@pytest.mark.parametrize(
    ("field", "replacement", "message"),
    (
        ("license", "Unknown", "Unreviewed license"),
        ("license", "GPL-3.0-only", "GPL-only component"),
        ("version", "0.0.0", "Version mismatch"),
    ),
)
def test_third_party_inventory_fails_closed(
    tmp_path: Path, monkeypatch, field: str, replacement: str, message: str,
) -> None:
    generator = _load("generate_third_party_licenses")
    value = json.loads((PACKAGING / "third-party-components.json").read_text(encoding="utf-8"))
    value["components"][0][field] = replacement
    manifest = tmp_path / "third-party-components.json"
    manifest.write_text(json.dumps(value), encoding="utf-8")
    monkeypatch.setattr(generator, "MANIFEST_PATH", manifest)
    with pytest.raises(generator.LicenseBundleError, match=message):
        generator.render()


def test_qt_source_manifest_is_fixed_and_complete() -> None:
    value = json.loads((PACKAGING / "qt-source-assets.json").read_text(encoding="utf-8"))
    assert value["schema_version"] == 1
    assert value["qt_version"] == value["pyside_version"] == "6.11.1"
    assert {item["name"]: item["bytes"] for item in value["assets"]} == EXPECTED_SOURCES
    assert sum(EXPECTED_SOURCES.values()) == 111823580
    for item in value["assets"]:
        assert item["url"].startswith("https://download.qt.io/")
        assert len(item["sha256"]) == 64
        assert not set(item["sha256"]) - set("0123456789abcdef")


def test_qt_bundle_classifier_fails_for_unreviewed_modules() -> None:
    audit = _load("audit_qt_bundle")
    assert audit.classify_qt_entry(r"PySide6\Qt6Core.dll") == "qtbase"
    assert audit.classify_qt_entry(r"PySide6\Qt6QuickTimeline.dll") == "qtquicktimeline"
    assert audit.classify_qt_entry(r"PySide6\plugins\imageformats\qsvg.dll") == "qtsvg"
    with pytest.raises(audit.QtBundleError, match="Unreviewed"):
        audit.classify_qt_entry(r"PySide6\Qt6Unknown.dll")


def test_release_asset_preparer_writes_fixed_checksum_set(tmp_path: Path, monkeypatch) -> None:
    prepare = _load("prepare_release_assets")
    assert prepare.SETUP_NAME == "Holon-0.2.0-alpha-Setup.exe"

    def fake_download(url: str, destination: Path, size: int, digest: str) -> None:
        assert url.startswith("https://download.qt.io/")
        assert size == EXPECTED_SOURCES[destination.name]
        assert len(digest) == 64
        destination.write_bytes(destination.name.encode("ascii"))

    monkeypatch.setattr(prepare, "_download", fake_download)
    setup = tmp_path / prepare.SETUP_NAME
    setup.write_bytes(b"setup")
    destination = tmp_path / "release"
    results = prepare.prepare(setup, destination)
    expected_assets = {
        prepare.SETUP_NAME, prepare.CHECKSUM_NAME, *EXPECTED_SOURCES,
    }
    assert len(expected_assets) == 8
    assert [item[0] for item in results] == [
        prepare.SETUP_NAME, *EXPECTED_SOURCES,
    ]
    assert {path.name for path in destination.iterdir()} == expected_assets
    checksum_lines = (
        destination / prepare.CHECKSUM_NAME
    ).read_text(encoding="ascii").splitlines()
    assert len(checksum_lines) == 7
    assert checksum_lines[0].endswith(f"  {prepare.SETUP_NAME}")


def test_source_download_keeps_tls_verification_enabled(tmp_path: Path, monkeypatch) -> None:
    prepare = _load("prepare_release_assets")
    payload = b"verified-source"
    digest = hashlib.sha256(payload).hexdigest()
    observed: dict[str, object] = {}

    def fake_urlopen(request, *, timeout: int, context: ssl.SSLContext):
        observed.update(request=request, timeout=timeout, context=context)
        return io.BytesIO(payload)

    monkeypatch.setattr(prepare.urllib.request, "urlopen", fake_urlopen)
    target = tmp_path / "source.tar.xz"
    prepare._download(
        "https://download.qt.io/source.tar.xz", target, len(payload), digest,
    )
    assert target.read_bytes() == payload
    assert observed["timeout"] == 60
    context = observed["context"]
    assert isinstance(context, ssl.SSLContext)
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


def test_release_documents_use_public_links_and_plain_authorship() -> None:
    public_documents = (
        ROOT / "README.md", ROOT / "docs" / "ARCHITECTURE.md",
        PACKAGING / "INSTALL.md", ROOT / "NOTICE",
    )
    for path in public_documents:
        text = path.read_text(encoding="utf-8")
        assert "—" not in text and "–" not in text
        assert "M5.09" not in text and "DEMO.md" not in text
        for raw_target in re.findall(r"!?\[[^]]*\]\(([^)]+)\)", text):
            target = raw_target.strip().strip("<>").split("#", 1)[0]
            if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
                continue
            assert (path.parent / unquote(target)).exists(), (
                f"Broken relative link in {path.relative_to(ROOT)}: {raw_target}"
            )
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    install = (PACKAGING / "INSTALL.md").read_text(encoding="utf-8")
    assert "/releases/tag/v0.2.0-alpha" in readme
    assert "/releases/download/v0.2.0-alpha/Holon-0.2.0-alpha-Setup.exe" in readme
    assert "unsigned" in readme.lower() and "SHA256SUMS.txt" in install
    assert "LGPLv3" in install and "%LOCALAPPDATA%\\Holon\\app\\licenses" in install
