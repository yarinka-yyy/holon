from __future__ import annotations

import json
from pathlib import Path

from holon_installation import PackageBuilder
from holon_modules import build_composition
from package_support import SOURCE_ROOT
from powershell_support import fake_hermes, invoke


MOCK_ROOT = SOURCE_ROOT / "modules" / "mock"


def _package(root: Path, name: str, *, mock: bool = False, disabled: bool = False):
    composition = root / f"composition-{name}"
    module_roots = [MOCK_ROOT] if mock else []
    disabled_ids = ["holon.mock"] if disabled else []
    build_composition(
        composition, name, module_roots, disabled_module_ids=disabled_ids,
    )
    artifacts = root / f"artifacts-{name}"
    artifacts.mkdir()
    guard, wallet = artifacts / "guard.exe", artifacts / "wallet.exe"
    guard.write_bytes(b"mock-guard-binary")
    wallet.write_bytes(b"mock-wallet-binary")
    package = root / f"package-{name}"
    manifest = PackageBuilder(SOURCE_ROOT, composition).build(
        package, {"guard": guard, "wallet": wallet}, test_fixture=True,
    )
    return composition, package, manifest


def _install(package: Path, local: Path, hermes: Path, *extra: object):
    return invoke(
        package / "install.ps1", "-PackageRoot", package,
        "-LocalAppDataRoot", local, "-HermesHome", hermes,
        "-ConfirmHermesClosed", *extra,
    )


def _compatible_hermes(home: Path) -> None:
    metadata = home / "hermes-agent" / "venv" / "Lib" / "site-packages" / (
        "hermes_agent-0.18.2.dist-info"
    )
    metadata.mkdir(parents=True)
    (metadata / "METADATA").write_text(
        "Name: hermes-agent\nVersion: 0.18.2\n", encoding="utf-8",
    )


def test_base_mock_and_disabled_packages_have_exact_optional_surface(tmp_path: Path) -> None:
    base_composition, base, base_manifest = _package(tmp_path, "base")
    mock_composition, mock, mock_manifest = _package(tmp_path, "mock", mock=True)
    _, disabled, disabled_manifest = _package(
        tmp_path, "mock-disabled", mock=True, disabled=True,
    )

    for composition, package in ((base_composition, base), (mock_composition, mock)):
        expected = (composition / "module-catalog.json").read_bytes()
        assert {
            (package / relative).read_bytes()
            for relative in (
                "payload/app/module-catalog.json",
                "payload/plugin/module-catalog.json",
                "payload/plugin/holon_modules/module-catalog.json",
            )
        } == {expected}

    assert base_manifest.module_ids == ()
    assert base_manifest.skill_ids == ("holon", "holon-earn", "holon-lending")
    assert "holon_mock_status" not in (
        base / "payload/plugin/plugin.yaml"
    ).read_text(encoding="utf-8")
    assert not (base / "payload/plugin/modules").exists()

    assert mock_manifest.module_ids == ("holon.mock",)
    assert mock_manifest.skill_ids == (
        "holon", "holon-earn", "holon-lending", "holon-mock",
    )
    assert "holon_mock_status" in (
        mock / "payload/plugin/plugin.yaml"
    ).read_text(encoding="utf-8")
    assert (mock / "payload/plugin/modules/holon.mock/hermes-tools.json").is_file()
    mock_skill = "payload/skills/crypto/holon-mock/SKILL.md"
    assert next(item for item in mock_manifest.files if item.path == mock_skill).critical

    assert disabled_manifest.module_ids == ("holon.mock",)
    assert disabled_manifest.skill_ids == ("holon", "holon-earn", "holon-lending")
    assert "holon_mock_status" not in (
        disabled / "payload/plugin/plugin.yaml"
    ).read_text(encoding="utf-8")
    disabled_module = disabled / "payload/plugin/modules/holon.mock"
    assert {path.name for path in disabled_module.iterdir()} == {"module-manifest.json"}
    assert not (disabled / "payload/skills/crypto/holon-mock").exists()


def test_base_mock_base_install_removes_all_stale_optional_surfaces(tmp_path: Path) -> None:
    _, base, _ = _package(tmp_path, "base")
    _, mock, _ = _package(tmp_path, "mock", mock=True)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    data_canary = local / "Holon" / "data" / "vault.canary"

    assert _install(base, local, hermes)[0] == 0
    data_canary.write_bytes(b"preserve")
    assert _install(mock, local, hermes)[0] == 0
    plugin = hermes / "plugins" / "holon"
    skills = hermes / "skills" / "crypto"
    assert (plugin / "modules/holon.mock/hermes-tools.json").is_file()
    assert (skills / "holon-mock/SKILL.md").is_file()
    assert "holon_mock_status" in (plugin / "plugin.yaml").read_text(encoding="utf-8")

    assert _install(base, local, hermes)[0] == 0
    assert not (plugin / "modules").exists()
    assert not (skills / "holon-mock").exists()
    assert "holon_mock_status" not in (plugin / "plugin.yaml").read_text(encoding="utf-8")
    assert data_canary.read_bytes() == b"preserve"


def test_invalid_previous_dynamic_ownership_stops_before_mutation(tmp_path: Path) -> None:
    _, base, _ = _package(tmp_path, "base")
    _, mock, _ = _package(tmp_path, "mock", mock=True)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    assert _install(mock, local, hermes)[0] == 0
    app = local / "Holon" / "app"
    canary = app / "HolonGuard.exe"
    canary.write_bytes(b"existing-canary")
    installed_manifest = app / "release-manifest.json"
    value = json.loads(installed_manifest.read_text(encoding="utf-8"))
    value["skill_ids"].append("holon-../foreign")
    installed_manifest.write_text(json.dumps(value), encoding="utf-8")

    code, result = _install(base, local, hermes)

    assert code == 2 and result["code"] == "INSTALL_VALIDATION_FAILED"
    assert canary.read_bytes() == b"existing-canary"
    assert (hermes / "skills/crypto/holon-mock/SKILL.md").is_file()


def test_failed_mock_to_base_enable_restores_whole_mock_composition(tmp_path: Path) -> None:
    _, base, _ = _package(tmp_path, "base")
    _, mock, _ = _package(tmp_path, "mock", mock=True)
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    assert _install(mock, local, hermes)[0] == 0
    _compatible_hermes(hermes)
    command = fake_hermes(
        tmp_path / "hermes-fail.ps1", fail_enable=True,
        enable_output="Plugin 'holon' is not installed or bundled.",
    )

    code, result = _install(
        base, local, hermes, "-HermesCommand", command, "-EnableHermesPlugin",
    )

    assert code == 3 and result["code"] == "HERMES_ENABLE_PLUGIN_NOT_FOUND"
    plugin = hermes / "plugins" / "holon"
    assert "holon_mock_status" in (plugin / "plugin.yaml").read_text(encoding="utf-8")
    assert (plugin / "modules/holon.mock/hermes-tools.json").is_file()
    assert (hermes / "skills/crypto/holon-mock/SKILL.md").is_file()


def test_legacy_v2_ownership_migrates_only_fixed_base_skills(tmp_path: Path) -> None:
    _, base, _ = _package(tmp_path, "base")
    local, hermes = tmp_path / "local", tmp_path / "hermes"
    app = local / "Holon" / "app"
    app.mkdir(parents=True)
    (app / "HolonGuard.exe").write_bytes(b"legacy")
    (app / "release-manifest.json").write_text(json.dumps({
        "manifest_version": "2",
        "package_version": "0.1.0a0",
        "component_versions": {},
        "hermes_compatibility": ">=0.18.2,<0.19.0",
        "files": [],
    }), encoding="utf-8")
    skills = hermes / "skills" / "crypto"
    for skill_id in ("holon", "holon-lending", "holon-private"):
        path = skills / skill_id / "SKILL.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(skill_id, encoding="utf-8")

    assert _install(base, local, hermes)[0] == 0
    assert (skills / "holon/SKILL.md").read_text(encoding="utf-8") != "holon"
    assert (skills / "holon-earn/SKILL.md").is_file()
    assert (skills / "holon-lending/SKILL.md").read_text(encoding="utf-8") != "holon-lending"
    assert (skills / "holon-private/SKILL.md").read_text(encoding="utf-8") == "holon-private"
