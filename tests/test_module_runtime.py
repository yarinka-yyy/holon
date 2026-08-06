from __future__ import annotations

from dataclasses import replace
import hashlib
from pathlib import Path
import shutil

from holon_modules import (
    CAPABILITY_DUPLICATE,
    MODULE_DISABLED,
    MODULE_INCOMPATIBLE,
    MODULE_INTEGRITY_FAILED,
    MODULE_LOAD_FAILED,
    ModuleCatalog,
    ModuleCatalogEntry,
    ModuleLifecycleState,
    build_composition,
    decode_catalog,
    decode_manifest,
    encode_catalog,
    encode_manifest,
    load_registry,
)


ROOT = Path(__file__).parents[1]
MOCK_ROOT = ROOT / "modules" / "mock"


def _rewrite_manifest(composition: Path, transform) -> None:
    catalog_path = composition / "module-catalog.json"
    catalog = decode_catalog(catalog_path.read_bytes())
    entry = catalog.modules[0]
    manifest_path = composition / entry.manifest_path
    manifest = transform(decode_manifest(manifest_path.read_bytes()))
    raw = encode_manifest(manifest)
    manifest_path.write_bytes(raw)
    replacement = replace(entry, manifest_sha256=hashlib.sha256(raw).hexdigest())
    catalog_path.write_bytes(encode_catalog(replace(catalog, modules=(replacement,))))


def test_ready_registry_imports_only_current_component(tmp_path: Path, monkeypatch) -> None:
    composition = tmp_path / "mock"
    build_composition(composition, "mock", [MOCK_ROOT])
    monkeypatch.syspath_prepend(str(MOCK_ROOT / "src"))

    registry = load_registry(composition / "module-catalog.json", "guard")

    assert registry.composition_id == "mock"
    assert registry.module_status("holon.mock").state is ModuleLifecycleState.READY
    capability = registry.resolve("holon.mock.read")
    assert capability.contribution("status", {}) == {
        "module_id": "holon.mock", "network_used": False, "status": "READY",
    }
    assert [item.declaration.capability_id for item in registry.capabilities()] == [
        "holon.mock.read",
    ]


def test_absent_disabled_and_incompatible_never_import(tmp_path: Path) -> None:
    calls: list[str] = []

    def importer(value: str):
        calls.append(value)
        raise AssertionError("must not import")

    base = tmp_path / "base"
    build_composition(base, "base")
    assert load_registry(base / "module-catalog.json", "guard", importer=importer).module_status(
        "holon.mock"
    ).state is ModuleLifecycleState.ABSENT

    disabled = tmp_path / "disabled"
    build_composition(
        disabled, "mock", [MOCK_ROOT], disabled_module_ids=["holon.mock"],
    )
    disabled_registry = load_registry(
        disabled / "module-catalog.json", "guard", importer=importer,
    )
    assert disabled_registry.module_status("holon.mock").code == MODULE_DISABLED

    incompatible = tmp_path / "incompatible"
    build_composition(incompatible, "mock", [MOCK_ROOT])
    _rewrite_manifest(
        incompatible, lambda manifest: replace(manifest, core_api_version="2"),
    )
    incompatible_registry = load_registry(
        incompatible / "module-catalog.json", "guard", importer=importer,
    )
    assert incompatible_registry.module_status("holon.mock").code == MODULE_INCOMPATIBLE
    assert calls == []


def test_integrity_and_factory_failure_are_contained(tmp_path: Path) -> None:
    damaged = tmp_path / "damaged"
    build_composition(damaged, "mock", [MOCK_ROOT])
    (damaged / "modules" / "holon.mock" / "src" / "holon_mock" / "guard.py").write_bytes(
        b"tampered"
    )
    invalid = load_registry(damaged / "module-catalog.json", "guard")
    assert invalid.module_status("holon.mock").state is ModuleLifecycleState.INVALID
    assert invalid.module_status("holon.mock").code == MODULE_INTEGRITY_FAILED

    degraded = tmp_path / "degraded"
    build_composition(degraded, "mock", [MOCK_ROOT])

    def fail_import(_entry_point: str):
        raise RuntimeError("private detail")

    failed = load_registry(
        degraded / "module-catalog.json", "guard", importer=fail_import,
    )
    assert failed.module_status("holon.mock").state is ModuleLifecycleState.DEGRADED
    assert failed.module_status("holon.mock").code == MODULE_LOAD_FAILED
    assert failed.capabilities() == ()


def test_duplicate_capability_invalidates_both_modules(tmp_path: Path) -> None:
    second = tmp_path / "second-source"
    shutil.copytree(MOCK_ROOT, second)
    manifest_path = second / "module-manifest.json"
    manifest = decode_manifest(manifest_path.read_bytes())
    manifest_path.write_bytes(encode_manifest(replace(
        manifest, module_id="holon.second", display_name="Holon Second",
    )))
    composition = tmp_path / "duplicates"
    build_composition(composition, "mock", [MOCK_ROOT, second])

    registry = load_registry(
        composition / "module-catalog.json", "guard", importer=lambda value: value,
    )

    assert registry.module_status("holon.mock").code == CAPABILITY_DUPLICATE
    assert registry.module_status("holon.second").code == CAPABILITY_DUPLICATE
    assert registry.capabilities() == ()


def test_catalog_digest_mismatch_disables_only_optional_capabilities(tmp_path: Path) -> None:
    composition = tmp_path / "mismatch"
    build_composition(composition, "mock", [MOCK_ROOT])
    catalog_path = composition / "module-catalog.json"
    catalog = decode_catalog(catalog_path.read_bytes())
    entry = replace(catalog.modules[0], manifest_sha256="0" * 64)
    catalog_path.write_bytes(encode_catalog(ModuleCatalog(
        catalog.composition_id, catalog.core_api_version, (entry,),
    )))

    registry = load_registry(catalog_path, "guard")

    assert registry.module_status("holon.mock").code == MODULE_INTEGRITY_FAILED
    assert registry.capabilities() == ()
