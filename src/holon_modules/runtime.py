"""Bounded module verification and component capability registration."""

from __future__ import annotations

import hashlib
import importlib
import os
from pathlib import Path, PurePosixPath
import stat
from typing import Callable

from .codec import decode_catalog, decode_manifest
from .model import (
    CAPABILITY_DUPLICATE,
    CAPABILITY_UNAVAILABLE,
    CORE_API_VERSION,
    MODULE_CATALOG_INVALID,
    MODULE_DISABLED,
    MODULE_INCOMPATIBLE,
    MODULE_INTEGRITY_FAILED,
    MODULE_LOAD_FAILED,
    MODULE_MANIFEST_INVALID,
    ModuleContractError,
    ModuleLifecycleState,
    ModuleManifest,
    ModuleStatus,
    RegisteredCapability,
)

Importer = Callable[[str], object]


def default_catalog_path() -> Path:
    return Path(__file__).with_name("module-catalog.json")


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _safe_file(root: Path, relative: str) -> Path:
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        lexical_root = Path(os.path.abspath(root))
        lexical_candidate = Path(os.path.abspath(candidate))
        lexical_candidate.relative_to(lexical_root)
        root_resolved = root.resolve(strict=True)
        candidate_resolved = candidate.resolve(strict=True)
        candidate_resolved.relative_to(root_resolved)
        current = lexical_candidate
        while True:
            details = current.lstat()
            attributes = getattr(details, "st_file_attributes", 0)
            if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
                raise OSError("Module file uses a link")
            if current == lexical_root:
                break
            current = current.parent
    except (OSError, ValueError) as exc:
        raise ModuleContractError(
            MODULE_INTEGRITY_FAILED, "Module file is unavailable",
        ) from exc
    if not candidate_resolved.is_file():
        raise ModuleContractError(MODULE_INTEGRITY_FAILED, "Module file is unavailable")
    return candidate_resolved


def verify_manifest_files(
    root: Path,
    manifest: ModuleManifest,
    targets: frozenset[str] | None = None,
) -> None:
    for item in manifest.files:
        if targets is not None and not targets.intersection(item.targets):
            continue
        try:
            raw = _safe_file(root, item.path).read_bytes()
        except OSError as exc:
            raise ModuleContractError(
                MODULE_INTEGRITY_FAILED, "Module file is unavailable",
            ) from exc
        if _digest(raw) != item.sha256:
            raise ModuleContractError(
                MODULE_INTEGRITY_FAILED, "Module file digest mismatch",
            )


def _import_contribution(entry_point: str) -> object:
    module_name, attribute = entry_point.split(":", 1)
    module = importlib.import_module(module_name)
    factory = getattr(module, attribute)
    if not callable(factory):
        raise TypeError("Module entry point is not callable")
    return factory()


class CapabilityRegistry:
    """Immutable-by-convention view of one component's active capabilities."""

    def __init__(
        self,
        statuses: dict[str, ModuleStatus] | None = None,
        capabilities: dict[str, RegisteredCapability] | None = None,
        *,
        catalog_error: str | None = None,
        composition_id: str = "base",
    ) -> None:
        self._statuses = dict(statuses or {})
        self._capabilities = dict(capabilities or {})
        self.catalog_error = catalog_error
        self.composition_id = composition_id

    def module_status(self, module_id: str) -> ModuleStatus:
        return self._statuses.get(module_id, ModuleStatus(
            module_id,
            ModuleLifecycleState.ABSENT,
            "MODULE_ABSENT",
            "Optional module is not part of this composition.",
        ))

    def statuses(self) -> tuple[ModuleStatus, ...]:
        return tuple(self._statuses[key] for key in sorted(self._statuses))

    def capabilities(self, kind: str | None = None) -> tuple[RegisteredCapability, ...]:
        values = (
            item for key, item in sorted(self._capabilities.items())
            if kind is None or item.declaration.kind == kind
        )
        return tuple(values)

    def resolve(self, capability_id: str) -> RegisteredCapability:
        try:
            return self._capabilities[capability_id]
        except KeyError as exc:
            raise ModuleContractError(
                CAPABILITY_UNAVAILABLE, "Module capability is unavailable",
            ) from exc


def load_registry(
    catalog_path: Path,
    component: str,
    *,
    importer: Importer = _import_contribution,
    core_api_version: str = CORE_API_VERSION,
) -> CapabilityRegistry:
    try:
        catalog_raw = catalog_path.read_bytes()
        catalog = decode_catalog(catalog_raw)
    except (OSError, ModuleContractError) as exc:
        code = exc.code if isinstance(exc, ModuleContractError) else MODULE_CATALOG_INVALID
        return CapabilityRegistry(catalog_error=code)
    if catalog.core_api_version != core_api_version:
        return CapabilityRegistry(
            catalog_error=MODULE_INCOMPATIBLE,
            composition_id=catalog.composition_id,
        )

    statuses: dict[str, ModuleStatus] = {}
    manifests: dict[str, ModuleManifest] = {}
    roots: dict[str, Path] = {}
    for entry in catalog.modules:
        if not entry.enabled:
            statuses[entry.module_id] = ModuleStatus(
                entry.module_id, ModuleLifecycleState.DISABLED, MODULE_DISABLED,
                "Optional module is disabled by the build composition.",
            )
            continue
        try:
            manifest_path = _safe_file(catalog_path.parent, entry.manifest_path)
            raw = manifest_path.read_bytes()
            if _digest(raw) != entry.manifest_sha256:
                raise ModuleContractError(
                    MODULE_INTEGRITY_FAILED, "Module manifest digest mismatch",
                )
            manifest = decode_manifest(raw)
            if manifest.module_id != entry.module_id:
                raise ModuleContractError(
                    MODULE_MANIFEST_INVALID, "Manifest module id mismatch",
                )
            if manifest.core_api_version != core_api_version:
                statuses[entry.module_id] = ModuleStatus(
                    entry.module_id, ModuleLifecycleState.INCOMPATIBLE,
                    MODULE_INCOMPATIBLE,
                    "Optional module requires a different Core API.",
                )
                continue
            root = manifest_path.parent
            targets = (
                frozenset({component})
                if component == "hermes"
                else frozenset({component, "shared"})
            )
            verify_manifest_files(root, manifest, targets)
            manifests[entry.module_id] = manifest
            roots[entry.module_id] = root
        except ModuleContractError as exc:
            statuses[entry.module_id] = ModuleStatus(
                entry.module_id, ModuleLifecycleState.INVALID, exc.code,
                "Optional module declaration or integrity is invalid.",
            )
        except OSError:
            statuses[entry.module_id] = ModuleStatus(
                entry.module_id, ModuleLifecycleState.INVALID,
                MODULE_INTEGRITY_FAILED,
                "Optional module declaration or integrity is invalid.",
            )

    owners: dict[str, list[str]] = {}
    for module_id, manifest in manifests.items():
        for declaration in manifest.capabilities:
            owners.setdefault(declaration.capability_id, []).append(module_id)
    duplicate_modules = {
        module_id
        for owner_ids in owners.values() if len(owner_ids) > 1
        for module_id in owner_ids
    }
    wallet_page_modules = {
        module_id for module_id, manifest in manifests.items()
        if any(item.kind == "wallet_page" for item in manifest.capabilities)
    }
    if len(wallet_page_modules) > 1:
        duplicate_modules.update(wallet_page_modules)
    for module_id in duplicate_modules:
        manifests.pop(module_id, None)
        roots.pop(module_id, None)
        statuses[module_id] = ModuleStatus(
            module_id, ModuleLifecycleState.INVALID, CAPABILITY_DUPLICATE,
            "Optional module capability id conflicts with another module.",
        )

    registered: dict[str, RegisteredCapability] = {}
    for module_id in sorted(manifests):
        manifest = manifests[module_id]
        selected = [
            declaration for declaration in manifest.capabilities
            if declaration.component == component
        ]
        pending: list[RegisteredCapability] = []
        try:
            for declaration in selected:
                contribution = (
                    importer(declaration.entry_point)
                    if declaration.entry_point is not None else None
                )
                pending.append(RegisteredCapability(
                    module_id, declaration, contribution, str(roots[module_id]),
                ))
        except Exception:
            statuses[module_id] = ModuleStatus(
                module_id, ModuleLifecycleState.DEGRADED, MODULE_LOAD_FAILED,
                "Optional module could not initialize this component.",
            )
            continue
        for capability in pending:
            registered[capability.declaration.capability_id] = capability
        statuses[module_id] = ModuleStatus(
            module_id, ModuleLifecycleState.READY, "MODULE_READY",
            "Optional module is ready.",
        )
    return CapabilityRegistry(
        statuses,
        registered,
        composition_id=catalog.composition_id,
    )
