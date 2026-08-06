from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path

import pytest

from holon_modules import (
    MODULE_CATALOG_INVALID,
    MODULE_MANIFEST_INVALID,
    ModuleCatalog,
    ModuleContractError,
    decode_catalog,
    decode_manifest,
    encode_catalog,
    encode_manifest,
)


ROOT = Path(__file__).parents[1]
MOCK_ROOT = ROOT / "modules" / "mock"


def test_manifest_and_base_catalog_are_canonical() -> None:
    manifest_raw = (MOCK_ROOT / "module-manifest.json").read_bytes()
    manifest = decode_manifest(manifest_raw)
    assert manifest.module_id == "holon.mock"
    assert encode_manifest(manifest) == manifest_raw

    catalog_raw = (ROOT / "src" / "holon_modules" / "module-catalog.json").read_bytes()
    catalog = decode_catalog(catalog_raw)
    assert catalog == ModuleCatalog("base", "1", ())
    assert encode_catalog(catalog) == catalog_raw


@pytest.mark.parametrize(
    ("mutate", "code"),
    [
        (lambda value: {**value, "unexpected": True}, MODULE_MANIFEST_INVALID),
        (lambda value: {**value, "module_version": "latest"}, MODULE_MANIFEST_INVALID),
        (lambda value: {**value, "display_name": "Mock\nModule"}, MODULE_MANIFEST_INVALID),
        (
            lambda value: {
                **value,
                "capabilities": [
                    {**value["capabilities"][0], "descriptor": {"weight": 0.5}},
                    *value["capabilities"][1:],
                ],
            },
            MODULE_MANIFEST_INVALID,
        ),
    ],
)
def test_manifest_refuses_unknown_or_unsafe_values(mutate, code: str) -> None:
    value = json.loads((MOCK_ROOT / "module-manifest.json").read_text(encoding="utf-8"))
    raw = (json.dumps(mutate(value), separators=(",", ":"), sort_keys=True) + "\n").encode()
    with pytest.raises(ModuleContractError) as failure:
        decode_manifest(raw)
    assert failure.value.code == code


def test_noncanonical_json_and_bom_are_refused() -> None:
    value = json.loads((MOCK_ROOT / "module-manifest.json").read_text(encoding="utf-8"))
    for raw in (
        json.dumps(value, indent=2).encode(),
        b"\xef\xbb\xbf" + encode_manifest(decode_manifest(
            (MOCK_ROOT / "module-manifest.json").read_bytes()
        )),
    ):
        with pytest.raises(ModuleContractError) as failure:
            decode_manifest(raw)
        assert failure.value.code == MODULE_MANIFEST_INVALID


@pytest.mark.parametrize("unsafe_path", ["CON", "wallet/bad?.qml", "wallet/trailing. "])
def test_manifest_refuses_windows_unsafe_paths(unsafe_path: str) -> None:
    value = json.loads((MOCK_ROOT / "module-manifest.json").read_text(encoding="utf-8"))
    value["files"][0]["path"] = unsafe_path
    raw = (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()
    with pytest.raises(ModuleContractError) as failure:
        decode_manifest(raw)
    assert failure.value.code == MODULE_MANIFEST_INVALID


def test_catalog_refuses_duplicate_and_noncanonical_entries() -> None:
    catalog = ModuleCatalog("mock", "1", ())
    value = catalog.to_dict()
    value["unexpected"] = True
    raw = (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()
    with pytest.raises(ModuleContractError) as failure:
        decode_catalog(raw)
    assert failure.value.code == MODULE_CATALOG_INVALID


def test_manifest_api_version_is_data_until_runtime_compatibility_check() -> None:
    manifest = decode_manifest((MOCK_ROOT / "module-manifest.json").read_bytes())
    incompatible = replace(manifest, core_api_version="2")
    assert decode_manifest(encode_manifest(incompatible)).core_api_version == "2"
