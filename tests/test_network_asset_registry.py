from __future__ import annotations

import json
import base64
import hashlib
import re
from copy import deepcopy
from pathlib import Path

import pytest

from holon_contracts.registry import RegistryError, load_registry


SOURCE = Path("src/holon_contracts/network-assets.json")


def _raw() -> dict:
    return json.loads(SOURCE.read_text(encoding="utf-8"))


def _reject(tmp_path: Path, value: dict) -> None:
    path = tmp_path / "registry.json"
    path.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(RegistryError):
        load_registry(path)


def test_project_registry_is_ordered_strict_and_complete() -> None:
    value = load_registry()
    assert [item.network_id for item in value.networks] == [
        "ethereum", "base", "arbitrum", "optimism",
    ]
    assert [item.chain_id for item in value.networks] == [1, 8453, 42161, 10]
    assert len(value.assets) == 10
    assert len(value.deployments) == 26
    assert all(
        (Path("src/holon_wallet") / item.icon_path).is_file()
        for item in (*value.networks, *value.assets)
    )


@pytest.mark.parametrize("mutation", [
    "duplicate_network", "duplicate_chain", "bad_address", "duplicate_address",
    "unknown_capability", "missing_provenance", "bad_metadata", "bad_native",
])
def test_registry_rejects_identity_metadata_capability_and_provenance_errors(
    tmp_path: Path, mutation: str,
) -> None:
    value = deepcopy(_raw())
    if mutation == "duplicate_network":
        value["networks"][1]["network_id"] = "ethereum"
    elif mutation == "duplicate_chain":
        value["networks"][1]["chain_id"] = 1
    elif mutation == "bad_address":
        value["deployments"][1]["contract_address"] = "0x1234"
    elif mutation == "duplicate_address":
        value["deployments"][2]["contract_address"] = value["deployments"][1]["contract_address"]
    elif mutation == "unknown_capability":
        value["deployments"][1]["capabilities"].append("swap")
    elif mutation == "missing_provenance":
        value["deployments"][1]["source_revision"] = ""
    elif mutation == "bad_metadata":
        value["assets"][0]["display_symbol"] = ""
    elif mutation == "bad_native":
        value["networks"][0]["native_asset_id"] = "weth"
    _reject(tmp_path, value)


def test_registry_is_not_mutable_through_loaded_maps() -> None:
    value = load_registry()
    with pytest.raises(TypeError):
        value.network_by_id["other"] = value.networks[0]  # type: ignore[index]


def test_new_networks_are_receive_only_and_do_not_expand_authority_routes() -> None:
    value = load_registry()
    transfer_routes = {
        item.deployment_id for item in value.deployments
        if "transfer" in item.capabilities
    }
    lending_routes = {
        item.deployment_id for item in value.deployments
        if "lending" in item.capabilities
    }

    assert transfer_routes == {
        "ethereum:eth", "ethereum:usdc", "base:eth", "base:usdc",
    }
    assert lending_routes == {"base:usdc"}


def test_official_png_wrappers_preserve_source_bytes_exactly() -> None:
    raw = _raw()
    expected = {
        item["icon"]["path"].rsplit("/", 1)[-1].removesuffix(".svg"): item["icon"]["source_sha256"]
        for item in (*raw["networks"], *raw["assets"])
        if item["icon"]["path"].rsplit("/", 1)[-1] in {
            "arbitrum.svg", "weth.svg", "op.svg", "cbbtc.svg",
        }
    }
    for name, digest in expected.items():
        text = Path(f"src/holon_wallet/qml/assets/{name}.svg").read_text(encoding="utf-8")
        encoded = re.search(r"base64,([^\"]+)", text)
        assert encoded is not None
        assert hashlib.sha256(base64.b64decode(encoded.group(1))).hexdigest() == digest
