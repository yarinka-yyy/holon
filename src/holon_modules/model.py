"""Strict, secret-free contracts for deterministic Holon modules."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping

MANIFEST_VERSION = "1"
CATALOG_VERSION = "1"
CORE_API_VERSION = "1"

MAX_CATALOG_BYTES = 64 * 1024
MAX_MANIFEST_BYTES = 256 * 1024
MAX_MODULES = 32
MAX_CAPABILITIES = 64
MAX_MODULE_FILES = 512
MAX_PATH_LENGTH = 240

CAPABILITY_KINDS = frozenset({
    "wallet_page",
    "public_reader",
    "earn_provider",
    "hermes_toolset",
    "protected_action_adapter",
})
COMPONENTS = frozenset({"guard", "hermes", "wallet"})
FILE_TARGETS = frozenset({"guard", "hermes", "shared", "skill", "wallet"})

MODULE_CATALOG_INVALID = "MODULE_CATALOG_INVALID"
MODULE_MANIFEST_INVALID = "MODULE_MANIFEST_INVALID"
MODULE_INTEGRITY_FAILED = "MODULE_INTEGRITY_FAILED"
MODULE_DISABLED = "MODULE_DISABLED"
MODULE_INCOMPATIBLE = "MODULE_INCOMPATIBLE"
MODULE_DUPLICATE = "MODULE_DUPLICATE"
CAPABILITY_DUPLICATE = "CAPABILITY_DUPLICATE"
MODULE_LOAD_FAILED = "MODULE_LOAD_FAILED"
CAPABILITY_UNAVAILABLE = "CAPABILITY_UNAVAILABLE"


class ModuleContractError(ValueError):
    """A module declaration or catalog is unsafe or incompatible."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ModuleLifecycleState(str, Enum):
    ABSENT = "ABSENT"
    DISABLED = "DISABLED"
    READY = "READY"
    DEGRADED = "DEGRADED"
    INCOMPATIBLE = "INCOMPATIBLE"
    INVALID = "INVALID"


@dataclass(frozen=True, slots=True)
class ModuleFile:
    path: str
    sha256: str
    targets: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "sha256": self.sha256,
            "targets": list(self.targets),
        }


@dataclass(frozen=True, slots=True)
class CapabilityDeclaration:
    capability_id: str
    kind: str
    version: str
    component: str
    entry_point: str | None
    descriptor: Mapping[str, object]

    def to_dict(self) -> dict[str, object]:
        return {
            "capability_id": self.capability_id,
            "kind": self.kind,
            "version": self.version,
            "component": self.component,
            "entry_point": self.entry_point,
            "descriptor": dict(self.descriptor),
        }


@dataclass(frozen=True, slots=True)
class ModuleManifest:
    module_id: str
    module_version: str
    display_name: str
    core_api_version: str
    components: tuple[str, ...]
    capabilities: tuple[CapabilityDeclaration, ...]
    files: tuple[ModuleFile, ...]
    manifest_version: str = MANIFEST_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "module_id": self.module_id,
            "module_version": self.module_version,
            "display_name": self.display_name,
            "core_api_version": self.core_api_version,
            "components": list(self.components),
            "capabilities": [item.to_dict() for item in self.capabilities],
            "files": [item.to_dict() for item in self.files],
        }


@dataclass(frozen=True, slots=True)
class ModuleCatalogEntry:
    module_id: str
    enabled: bool
    manifest_path: str
    manifest_sha256: str

    def to_dict(self) -> dict[str, object]:
        return {
            "module_id": self.module_id,
            "enabled": self.enabled,
            "manifest_path": self.manifest_path,
            "manifest_sha256": self.manifest_sha256,
        }


@dataclass(frozen=True, slots=True)
class ModuleCatalog:
    composition_id: str
    core_api_version: str
    modules: tuple[ModuleCatalogEntry, ...]
    catalog_version: str = CATALOG_VERSION

    def to_dict(self) -> dict[str, object]:
        return {
            "catalog_version": self.catalog_version,
            "composition_id": self.composition_id,
            "core_api_version": self.core_api_version,
            "modules": [item.to_dict() for item in self.modules],
        }


@dataclass(frozen=True, slots=True)
class ModuleStatus:
    module_id: str
    state: ModuleLifecycleState
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class RegisteredCapability:
    module_id: str
    declaration: CapabilityDeclaration
    contribution: object | None
    resource_root: str | None = None
