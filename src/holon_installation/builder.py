"""Deterministic release staging from injected Guard and Wallet artifacts."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
from typing import Mapping

from holon_modules import (
    CORE_API_VERSION,
    ModuleCatalog,
    ModuleLifecycleState,
    ModuleManifest,
    decode_catalog as decode_module_catalog,
    decode_manifest as decode_module_manifest,
    load_registry as load_module_registry,
    load_toolset,
    verify_manifest_files,
)

from .codec import encode_manifest
from .model import (
    BASE_SKILL_IDS, COMPONENT_VERSIONS, PACKAGE_VERSION, ReleaseFile, ReleaseManifest,
)


class BuildError(ValueError):
    pass


def _uses_link(path: Path) -> bool:
    current = Path(os.path.abspath(path))
    while True:
        details = current.lstat()
        attributes = getattr(details, "st_file_attributes", 0)
        if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            return True
        if current.parent == current:
            return False
        current = current.parent


def _write_json(path: Path, value: object) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n",
        encoding="utf-8", newline="\n",
    )


def _component(relative: str) -> str:
    if relative.startswith("payload/app/HolonGuard"):
        return "guard"
    if relative.startswith("payload/app/HolonWallet"):
        return "wallet"
    if relative in {
        "payload/app/module-catalog.json", "payload/plugin/module-catalog.json",
    }:
        return "modules"
    if relative.startswith("payload/app/holon_policy/"):
        return "policy"
    if relative.startswith("payload/plugin/holon_contracts/"):
        return "contracts"
    if relative.startswith("payload/plugin/holon_guard_ipc/"):
        return "contracts"
    if relative.startswith(("payload/plugin/holon_modules/", "payload/plugin/modules/")):
        return "modules"
    if relative.startswith("payload/plugin/"):
        return "plugin"
    if relative.startswith("payload/skills/"):
        return "skills"
    if relative.startswith("payload/initial-data/"):
        return "initial-data"
    return "installer"


def _critical(relative: str) -> bool:
    if relative.startswith(("payload/app/", "payload/plugin/")):
        return True
    parts = relative.split("/")
    return (
        len(parts) >= 5
        and parts[:3] == ["payload", "skills", "crypto"]
        and parts[3] not in BASE_SKILL_IDS
    )


class PackageBuilder:
    def __init__(self, source_root: Path, composition_root: Path | None = None) -> None:
        self.source_root = source_root
        self.composition_root = composition_root or source_root / "src" / "holon_modules"

    @staticmethod
    def _copy_file(source: Path, target: Path) -> None:
        try:
            details = source.lstat()
        except OSError as exc:
            raise BuildError("Required package artifact is unavailable") from exc
        if (
            not source.is_file() or _uses_link(source)
        ):
            raise BuildError("Required package artifact is unavailable")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    @staticmethod
    def _validate_production_artifact(kind: str, source: Path) -> None:
        expected = {"guard": "HolonGuard.exe", "wallet": "HolonWallet.exe"}[kind]
        if source.name != expected:
            raise BuildError(f"Production {kind} artifact name is invalid")
        try:
            details = source.lstat()
            with source.open("rb") as stream:
                dos_header = stream.read(0x40)
                if len(dos_header) != 0x40 or dos_header[:2] != b"MZ":
                    raise BuildError(f"Production {kind} artifact is invalid")
                pe_offset = struct.unpack_from("<I", dos_header, 0x3C)[0]
                if pe_offset < 0x40 or pe_offset + 26 > details.st_size:
                    raise BuildError(f"Production {kind} artifact is invalid")
                stream.seek(pe_offset)
                pe_header = stream.read(26)
                if len(pe_header) != 26:
                    raise BuildError(f"Production {kind} artifact is invalid")
        except OSError as exc:
            raise BuildError(f"Production {kind} artifact is unavailable") from exc
        if (
            not source.is_file() or _uses_link(source)
            or details.st_size < 0x100
        ):
            raise BuildError(f"Production {kind} artifact is invalid")
        if pe_header[:4] != b"PE\0\0":
            raise BuildError(f"Production {kind} artifact is invalid")
        machine = struct.unpack_from("<H", pe_header, 4)[0]
        characteristics = struct.unpack_from("<H", pe_header, 22)[0]
        optional_magic = struct.unpack_from("<H", pe_header, 24)[0]
        if machine != 0x8664 or not (characteristics & 0x0002) or optional_magic != 0x20B:
            raise BuildError(f"Production {kind} artifact is not Windows x64")

    def _active_hermes_tool_names(
        self,
        catalog: ModuleCatalog,
        manifests: Mapping[str, ModuleManifest],
    ) -> tuple[str, ...]:
        registry = load_module_registry(
            self.composition_root / "module-catalog.json", "hermes",
        )
        if registry.catalog_error is not None:
            raise BuildError("Module composition cannot initialize Hermes declarations")
        candidates: dict[str, tuple[str, ...]] = {}
        for capability in registry.capabilities("hermes_toolset"):
            module_id = capability.module_id
            if registry.module_status(module_id).state is not ModuleLifecycleState.READY:
                continue
            manifest = manifests[module_id]
            readers = {
                item.capability_id: item for item in manifest.capabilities
                if item.kind == "public_reader"
            }
            descriptor_path = str(capability.declaration.descriptor["descriptor_path"])
            try:
                declarations = load_toolset(
                    self.composition_root / "modules" / module_id / descriptor_path,
                )
            except Exception as exc:
                raise BuildError("Optional Hermes descriptor is invalid") from exc
            if any(
                item.capability_id not in readers
                or item.operation not in readers[item.capability_id].descriptor["operations"]
                for item in declarations
            ):
                raise BuildError("Optional Hermes descriptor references unavailable reader")
            candidates[module_id] = tuple(item.name for item in declarations)
        owners: dict[str, list[str]] = {}
        for module_id, names in candidates.items():
            for name in names:
                owners.setdefault(name, []).append(module_id)
        conflicts = {
            module_id
            for module_ids in owners.values() if len(module_ids) > 1
            for module_id in module_ids
        }
        return tuple(
            name
            for module_id in sorted(candidates) if module_id not in conflicts
            for name in candidates[module_id]
        )

    def _copy_plugin(
        self,
        root: Path,
        optional_tool_names: tuple[str, ...],
    ) -> None:
        plugin = self.source_root / "src" / "holon_hermes_plugin"
        for source in sorted(plugin.glob("*.py")):
            self._copy_file(source, root / "payload" / "plugin" / source.name)
        manifest_source = plugin / "plugin.yaml"
        try:
            text = manifest_source.read_text(encoding="utf-8")
        except (OSError, UnicodeError) as exc:
            raise BuildError("Hermes plugin manifest is unavailable") from exc
        marker = "provides_hooks:\n"
        if text.count(marker) != 1 or "provides_tools:\n" not in text:
            raise BuildError("Hermes plugin manifest layout is invalid")
        head, tail = text.split(marker, 1)
        static_names = tuple(
            line.removeprefix("  - ")
            for line in head.splitlines()
            if line.startswith("  - ")
        )
        if (
            len(static_names) != len(set(static_names))
            or set(static_names).intersection(optional_tool_names)
            or len(optional_tool_names) != len(set(optional_tool_names))
        ):
            raise BuildError("Hermes tool declaration conflicts with Base Holon")
        generated = (
            head
            + "".join(f"  - {name}\n" for name in optional_tool_names)
            + marker
            + tail
        )
        target = root / "payload" / "plugin" / "plugin.yaml"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(generated, encoding="utf-8", newline="\n")
        for package_name in (
            "holon_contracts", "holon_earn", "holon_guard_ipc", "holon_modules",
        ):
            package = self.source_root / "src" / package_name
            sources = sorted(package.glob("*.py"))
            if package_name == "holon_contracts":
                sources.append(package / "network-assets.json")
            for source in sources:
                self._copy_file(
                    source, root / "payload" / "plugin" / package_name / source.name,
                )

    def _copy_skills(
        self,
        root: Path,
        catalog: ModuleCatalog,
        manifests: Mapping[str, ModuleManifest],
    ) -> tuple[str, ...]:
        skills_root = self.source_root / "skills" / "crypto"
        skill_files = {path.parent.name: path for path in skills_root.glob("*/SKILL.md")}
        if set(skill_files) != {"holon", "holon-earn", "holon-lending"}:
            raise BuildError("Holon skill source set is invalid")
        for name in sorted(skill_files):
            self._copy_file(
                skill_files[name],
                root / "payload" / "skills" / "crypto" / name / "SKILL.md",
            )
        active_ids: set[str] = set(skill_files)
        enabled = {entry.module_id for entry in catalog.modules if entry.enabled}
        for module_id in sorted(enabled):
            manifest = manifests[module_id]
            declared_skill_ids = {
                str(skill_id)
                for capability in manifest.capabilities
                if capability.kind == "hermes_toolset"
                for skill_id in capability.descriptor["skill_ids"]
            }
            copied_skill_ids: set[str] = set()
            for item in manifest.files:
                if "skill" not in item.targets:
                    continue
                parts = item.path.split("/")
                if (
                    len(parts) != 4
                    or parts[:2] != ["skills", "crypto"]
                    or parts[3] != "SKILL.md"
                    or parts[2] not in declared_skill_ids
                    or parts[2] in active_ids
                    or parts[2] in copied_skill_ids
                ):
                    raise BuildError("Optional module skill layout is invalid")
                active_ids.add(parts[2])
                copied_skill_ids.add(parts[2])
                self._copy_file(
                    self.composition_root / "modules" / module_id / item.path,
                    root / "payload" / item.path,
                )
            if copied_skill_ids != declared_skill_ids:
                raise BuildError("Optional module skill declaration is incomplete")
        return tuple(sorted(active_ids))

    def _load_composition(
        self,
    ) -> tuple[ModuleCatalog, bytes, dict[str, ModuleManifest]]:
        catalog_path = self.composition_root / "module-catalog.json"
        try:
            catalog_raw = catalog_path.read_bytes()
            catalog = decode_module_catalog(catalog_raw)
        except Exception as exc:
            raise BuildError("Module composition catalog is invalid") from exc
        if catalog.core_api_version != CORE_API_VERSION:
            raise BuildError("Module composition Core API is incompatible")
        manifests: dict[str, ModuleManifest] = {}
        for entry in catalog.modules:
            root = self.composition_root / "modules" / entry.module_id
            path = root / "module-manifest.json"
            try:
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != entry.manifest_sha256:
                    raise ValueError("manifest digest")
                manifest = decode_module_manifest(raw)
                if manifest.module_id != entry.module_id:
                    raise ValueError("manifest id")
                verify_manifest_files(root, manifest)
            except Exception as exc:
                raise BuildError("Module composition content is invalid") from exc
            manifests[entry.module_id] = manifest
        return catalog, catalog_raw, manifests

    def _copy_composition(
        self,
        root: Path,
        catalog: ModuleCatalog,
        catalog_raw: bytes,
        manifests: Mapping[str, ModuleManifest],
    ) -> None:
        for target in (
            root / "payload" / "app" / "module-catalog.json",
            root / "payload" / "plugin" / "module-catalog.json",
            root / "payload" / "plugin" / "holon_modules" / "module-catalog.json",
        ):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(catalog_raw)
        for entry in catalog.modules:
            source_root = self.composition_root / "modules" / entry.module_id
            target_root = root / "payload" / "plugin" / "modules" / entry.module_id
            self._copy_file(
                source_root / "module-manifest.json",
                target_root / "module-manifest.json",
            )
            if not entry.enabled:
                continue
            for item in manifests[entry.module_id].files:
                if "hermes" not in item.targets:
                    continue
                self._copy_file(
                    source_root.joinpath(*item.path.split("/")),
                    target_root.joinpath(*item.path.split("/")),
                )

    def _copy_licenses(self, root: Path) -> None:
        licenses = root / "payload" / "app" / "licenses"
        for source_name, target_name in (
            ("LICENSE", "LICENSE"),
            ("NOTICE", "NOTICE"),
            ("THIRD_PARTY_LICENSES.txt", "THIRD_PARTY_LICENSES.txt"),
        ):
            self._copy_file(self.source_root / source_name, licenses / target_name)

    def _initial_data(self, root: Path) -> None:
        data = root / "payload" / "initial-data"
        data.mkdir(parents=True, exist_ok=True)
        _write_json(data / "guard-state.json", {
            "action_fingerprint": None, "action_id": None, "flow_id": None,
            "owner_pid": None, "reason": "INSTALL_BOOTSTRAP", "state": "NORMAL",
            "state_version": 2, "updated_at": 0.0, "wallet_pid": None,
        })
        _write_json(data / "action-state.json", {
            "current": None, "state_version": 1, "terminal": [],
        })
        _write_json(data / "request-control-state.json", {
            "attempts": [], "block_fingerprint": None,
            "blocked_until": None, "state_version": 1,
        })
        (data / "journal.jsonl").write_bytes(b"")

    def build(
        self, destination: Path, artifacts: Mapping[str, Path], *, test_fixture: bool = False,
    ) -> ReleaseManifest:
        if set(artifacts) != {"guard", "wallet"}:
            raise BuildError("Guard and Wallet binaries are required")
        if not test_fixture:
            for kind, source in artifacts.items():
                self._validate_production_artifact(kind, source)
            if artifacts["guard"].resolve() == artifacts["wallet"].resolve():
                raise BuildError("Guard and Wallet artifacts must be distinct")
        if destination.exists() and any(destination.iterdir()):
            raise BuildError("Staging destination must be empty")
        destination.mkdir(parents=True, exist_ok=True)
        catalog, catalog_raw, manifests = self._load_composition()
        optional_tool_names = self._active_hermes_tool_names(catalog, manifests)
        for script in (
            "install.ps1", "uninstall.ps1", "detect-hermes.ps1",
            "InstallSupport.psm1", "INSTALL.md",
        ):
            self._copy_file(self.source_root / "packaging" / script, destination / script)
        self._copy_file(artifacts["guard"], destination / "payload" / "app" / "HolonGuard.exe")
        self._copy_file(artifacts["wallet"], destination / "payload" / "app" / "HolonWallet.exe")
        self._copy_file(
            self.source_root / "src" / "holon_policy" / "baseline-policy.json",
            destination / "payload" / "app" / "holon_policy" / "baseline-policy.json",
        )
        self._copy_licenses(destination)
        self._copy_plugin(destination, optional_tool_names)
        self._copy_composition(destination, catalog, catalog_raw, manifests)
        skill_ids = self._copy_skills(destination, catalog, manifests)
        self._initial_data(destination)
        files: list[ReleaseFile] = []
        for path in sorted(item for item in destination.rglob("*") if item.is_file()):
            relative = path.relative_to(destination).as_posix()
            component = _component(relative)
            critical = _critical(relative)
            files.append(ReleaseFile(component, relative, hashlib.sha256(path.read_bytes()).hexdigest(), critical))
        files.sort(key=lambda item: item.path.casefold())
        manifest = ReleaseManifest(
            PACKAGE_VERSION,
            COMPONENT_VERSIONS,
            tuple(files),
            composition_id=catalog.composition_id,
            core_api_version=catalog.core_api_version,
            module_catalog_sha256=hashlib.sha256(catalog_raw).hexdigest(),
            module_ids=tuple(entry.module_id for entry in catalog.modules),
            skill_ids=skill_ids,
        )
        (destination / "release-manifest.json").write_bytes(encode_manifest(manifest))
        from .verify import verify_package
        if not verify_package(destination).ok:
            raise BuildError("Built staging failed integrity verification")
        return manifest
