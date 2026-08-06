"""Public deterministic module contracts and runtime registry."""

from .codec import decode_catalog, decode_manifest, encode_catalog, encode_manifest
from .composition import build_composition
from .hermes import HermesToolDeclaration, load_toolset
from .model import (
    CAPABILITY_DUPLICATE,
    CAPABILITY_UNAVAILABLE,
    CATALOG_VERSION,
    CORE_API_VERSION,
    MANIFEST_VERSION,
    MODULE_CATALOG_INVALID,
    MODULE_DISABLED,
    MODULE_DUPLICATE,
    MODULE_INCOMPATIBLE,
    MODULE_INTEGRITY_FAILED,
    MODULE_LOAD_FAILED,
    MODULE_MANIFEST_INVALID,
    CapabilityDeclaration,
    ModuleCatalog,
    ModuleCatalogEntry,
    ModuleContractError,
    ModuleFile,
    ModuleLifecycleState,
    ModuleManifest,
    ModuleStatus,
    RegisteredCapability,
)
from .runtime import (
    CapabilityRegistry, default_catalog_path, load_registry, verify_manifest_files,
)

__all__ = [
    "CAPABILITY_DUPLICATE", "CAPABILITY_UNAVAILABLE", "CATALOG_VERSION",
    "CORE_API_VERSION", "MANIFEST_VERSION", "MODULE_CATALOG_INVALID",
    "MODULE_DISABLED", "MODULE_DUPLICATE", "MODULE_INCOMPATIBLE",
    "MODULE_INTEGRITY_FAILED", "MODULE_LOAD_FAILED", "MODULE_MANIFEST_INVALID",
    "CapabilityDeclaration", "CapabilityRegistry", "HermesToolDeclaration", "ModuleCatalog",
    "ModuleCatalogEntry", "ModuleContractError", "ModuleFile",
    "ModuleLifecycleState", "ModuleManifest", "ModuleStatus",
    "RegisteredCapability", "build_composition", "decode_catalog",
    "decode_manifest", "encode_catalog", "encode_manifest", "load_registry",
    "verify_manifest_files", "load_toolset", "default_catalog_path",
]
