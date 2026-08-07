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


def test_earn_provider_descriptor_has_fixed_category_and_sorted_networks() -> None:
    value = json.loads((MOCK_ROOT / "module-manifest.json").read_text(encoding="utf-8"))
    capability = {
        "capability_id": "holon.mock.earn",
        "component": "wallet",
        "descriptor": {
            "category": "VAULT", "network_ids": ["base", "hyperliquid"],
            "provider_id": "holon.mock.hyperliquid",
        },
        "entry_point": "holon_mock.wallet:create_view_model",
        "kind": "earn_provider",
        "version": "1",
    }
    value["capabilities"] = sorted(
        [*value["capabilities"], capability], key=lambda item: item["capability_id"],
    )
    raw = (json.dumps(value, separators=(",", ":"), sort_keys=True) + "\n").encode()
    assert any(item.kind == "earn_provider" for item in decode_manifest(raw).capabilities)

    paired = json.loads(raw)
    guard_capability = dict(capability)
    guard_capability.update({
        "capability_id": "holon.mock.earn.guard",
        "component": "guard",
        "entry_point": "holon_mock.guard:create_reader",
    })
    paired["capabilities"] = sorted(
        [*paired["capabilities"], guard_capability],
        key=lambda item: item["capability_id"],
    )
    paired_raw = (
        json.dumps(paired, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode()
    providers = [
        item for item in decode_manifest(paired_raw).capabilities
        if item.kind == "earn_provider"
    ]
    assert {item.component for item in providers} == {"guard", "wallet"}
    assert {item.descriptor["provider_id"] for item in providers} == {
        "holon.mock.hyperliquid",
    }

    for descriptor in (
        {"category": "RISK", "network_ids": ["base"], "provider_id": "holon.mock.vault"},
        {"category": "VAULT", "network_ids": ["hyperliquid", "base"], "provider_id": "holon.mock.vault"},
        {"category": "VAULT", "network_ids": [], "provider_id": "holon.mock.vault"},
        {"category": "VAULT", "network_ids": ["base"], "provider_id": "holon.other.vault"},
        {"category": "VAULT", "network_ids": ["base"], "provider_id": "holon.mock.vault", "extra": True},
        {
            "category": "VAULT", "network_ids": ["base"],
            "presentation": {"badge": "Vault", "logo_path": "wallet/logo.svg", "extra": True},
            "provider_id": "holon.mock.vault",
        },
    ):
        invalid = json.loads(raw)
        next(
            item for item in invalid["capabilities"]
            if item["capability_id"] == "holon.mock.earn"
        )["descriptor"] = descriptor
        encoded = (json.dumps(invalid, separators=(",", ":"), sort_keys=True) + "\n").encode()
        with pytest.raises(ModuleContractError) as failure:
            decode_manifest(encoded)
        assert failure.value.code == MODULE_MANIFEST_INVALID
