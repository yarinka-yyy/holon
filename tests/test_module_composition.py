from __future__ import annotations

from pathlib import Path

import pytest

from holon_modules import (
    MODULE_DUPLICATE,
    ModuleContractError,
    build_composition,
    decode_catalog,
    decode_manifest,
)


ROOT = Path(__file__).parents[1]
MOCK_ROOT = ROOT / "modules" / "mock"


def test_base_and_explicit_mock_compositions_are_deterministic(tmp_path: Path) -> None:
    base = tmp_path / "base"
    base_catalog = build_composition(base, "base")
    assert base_catalog.modules == ()
    assert decode_catalog((base / "module-catalog.json").read_bytes()) == base_catalog

    mock = tmp_path / "mock"
    mock_catalog = build_composition(mock, "mock", [MOCK_ROOT])
    assert [item.module_id for item in mock_catalog.modules] == ["holon.mock"]
    assert str(MOCK_ROOT) not in (mock / "module-catalog.json").read_text(encoding="utf-8")
    staged = mock / "modules" / "holon.mock"
    manifest = decode_manifest((staged / "module-manifest.json").read_bytes())
    assert {
        item.relative_to(staged).as_posix()
        for item in staged.rglob("*") if item.is_file()
    } == {"module-manifest.json", *(item.path for item in manifest.files)}


def test_disabled_module_must_be_an_explicit_source(tmp_path: Path) -> None:
    with pytest.raises(ModuleContractError):
        build_composition(tmp_path / "invalid", "mock", [], disabled_module_ids=["holon.mock"])


def test_duplicate_source_roots_are_refused_before_output(tmp_path: Path) -> None:
    destination = tmp_path / "duplicate"
    with pytest.raises(ModuleContractError) as failure:
        build_composition(destination, "mock", [MOCK_ROOT, MOCK_ROOT])
    assert failure.value.code == MODULE_DUPLICATE
    assert not destination.exists()


def test_nonempty_destination_is_preserved(tmp_path: Path) -> None:
    destination = tmp_path / "existing"
    destination.mkdir()
    canary = destination / "canary"
    canary.write_bytes(b"preserve")
    with pytest.raises(ModuleContractError):
        build_composition(destination, "base")
    assert canary.read_bytes() == b"preserve"


def test_invalid_composition_id_is_refused_before_output(tmp_path: Path) -> None:
    destination = tmp_path / "invalid-id"
    with pytest.raises(ModuleContractError):
        build_composition(destination, "../extended", [MOCK_ROOT])
    assert not destination.exists()


def test_existing_file_destination_is_preserved(tmp_path: Path) -> None:
    destination = tmp_path / "composition.json"
    destination.write_bytes(b"preserve")
    with pytest.raises(ModuleContractError):
        build_composition(destination, "base")
    assert destination.read_bytes() == b"preserve"
