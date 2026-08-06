"""Canonical JSON codecs and strict validation for module contracts."""

from __future__ import annotations

import json
from pathlib import PurePosixPath
import re
from typing import Any, Mapping

from .model import (
    CAPABILITY_KINDS,
    CATALOG_VERSION,
    COMPONENTS,
    FILE_TARGETS,
    MANIFEST_VERSION,
    MAX_CAPABILITIES,
    MAX_CATALOG_BYTES,
    MAX_MANIFEST_BYTES,
    MAX_MODULE_FILES,
    MAX_MODULES,
    MAX_PATH_LENGTH,
    MODULE_CATALOG_INVALID,
    MODULE_DUPLICATE,
    MODULE_MANIFEST_INVALID,
    CapabilityDeclaration,
    ModuleCatalog,
    ModuleCatalogEntry,
    ModuleContractError,
    ModuleFile,
    ModuleManifest,
)

MANIFEST_FIELDS = frozenset({
    "manifest_version", "module_id", "module_version", "display_name",
    "core_api_version", "components", "capabilities", "files",
})
CAPABILITY_FIELDS = frozenset({
    "capability_id", "kind", "version", "component", "entry_point", "descriptor",
})
FILE_FIELDS = frozenset({"path", "sha256", "targets"})
CATALOG_FIELDS = frozenset({
    "catalog_version", "composition_id", "core_api_version", "modules",
})
CATALOG_ENTRY_FIELDS = frozenset({
    "module_id", "enabled", "manifest_path", "manifest_sha256",
})

_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")
_SEMVER_RE = re.compile(
    r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_API_RE = re.compile(r"^[1-9][0-9]{0,7}$")
_ENTRY_POINT_RE = re.compile(
    r"^[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)*:[a-z_][a-z0-9_]*$"
)
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_WINDOWS_RESERVED = frozenset(
    {"con", "prn", "aux", "nul"}
    | {f"com{index}" for index in range(1, 10)}
    | {f"lpt{index}" for index in range(1, 10)}
)


def _error(code: str, message: str) -> ModuleContractError:
    return ModuleContractError(code, message)


def _object(value: Any, fields: frozenset[str], code: str, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or set(value) != fields:
        raise _error(code, f"Invalid {label} fields")
    return value


def _string(value: Any, *, maximum: int, code: str, label: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value.strip() != value
        or len(value) > maximum
        or any(ord(character) < 32 for character in value)
    ):
        raise _error(code, f"Invalid {label}")
    return value


def _identifier(value: Any, *, code: str, label: str) -> str:
    candidate = _string(value, maximum=64, code=code, label=label)
    if _ID_RE.fullmatch(candidate) is None:
        raise _error(code, f"Invalid {label}")
    return candidate


def _api_version(value: Any, code: str) -> str:
    candidate = _string(value, maximum=8, code=code, label="core API version")
    if _API_RE.fullmatch(candidate) is None:
        raise _error(code, "Invalid core API version")
    return candidate


def _digest(value: Any, code: str, label: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise _error(code, f"Invalid {label}")
    return value


def validate_relative_path(value: Any, *, code: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > MAX_PATH_LENGTH
        or "\\" in value
        or ":" in value
        or any(character in '<>"|?*' for character in value)
        or any(ord(character) < 32 for character in value)
    ):
        raise _error(code, "Invalid module path")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
        or any(part.endswith((" ", ".")) for part in path.parts)
        or any(part.split(".", 1)[0].casefold() in _WINDOWS_RESERVED for part in path.parts)
        or str(path) != value
    ):
        raise _error(code, "Invalid module path")
    return value


def _json_safe(value: Any, code: str, depth: int = 0) -> None:
    if depth > 12:
        raise _error(code, "Module metadata is too deep")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        if len(value) > 2048 or any(ord(character) < 32 for character in value):
            raise _error(code, "Invalid module metadata text")
        return
    if isinstance(value, float):
        raise _error(code, "Floating-point module metadata is not allowed")
    if isinstance(value, list):
        if len(value) > 256:
            raise _error(code, "Module metadata list is too large")
        for item in value:
            _json_safe(item, code, depth + 1)
        return
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise _error(code, "Module metadata object is too large")
        for key, item in value.items():
            if not isinstance(key, str):
                raise _error(code, "Invalid module metadata key")
            _json_safe(item, code, depth + 1)
        return
    raise _error(code, "Invalid module metadata value")


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _parse(raw: bytes, *, maximum: int, code: str) -> Any:
    if not raw or len(raw) > maximum or raw.startswith(b"\xef\xbb\xbf"):
        raise _error(code, "Invalid module JSON size or encoding")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _error(code, "Invalid module JSON") from exc
    _json_safe(value, code)
    return value


def _capability(value: Any) -> CapabilityDeclaration:
    code = MODULE_MANIFEST_INVALID
    raw = _object(value, CAPABILITY_FIELDS, code, "capability")
    capability_id = _identifier(raw["capability_id"], code=code, label="capability id")
    kind = _string(raw["kind"], maximum=40, code=code, label="capability kind")
    if kind not in CAPABILITY_KINDS:
        raise _error(code, "Invalid capability kind")
    version = _api_version(raw["version"], code)
    component = _string(raw["component"], maximum=16, code=code, label="component")
    if component not in COMPONENTS:
        raise _error(code, "Invalid component")
    entry_point = raw["entry_point"]
    if entry_point is not None:
        entry_point = _string(
            entry_point, maximum=160, code=code, label="entry point",
        )
        if _ENTRY_POINT_RE.fullmatch(entry_point) is None:
            raise _error(code, "Invalid entry point")
    if kind in {"hermes_toolset", "protected_action_adapter"}:
        if entry_point is not None:
            raise _error(code, "Capability cannot load executable code in M7")
    elif entry_point is None:
        raise _error(code, "Capability entry point is required")
    descriptor = raw["descriptor"]
    if not isinstance(descriptor, Mapping):
        raise _error(code, "Invalid capability descriptor")
    _json_safe(descriptor, code)
    if kind == "public_reader":
        descriptor = _object(
            descriptor, frozenset({"operations"}), code, "public reader descriptor",
        )
        operations = descriptor["operations"]
        if (
            not isinstance(operations, list)
            or not operations
            or len(operations) > 32
            or any(
                not isinstance(operation, str)
                or _ID_RE.fullmatch(operation) is None
                for operation in operations
            )
            or tuple(sorted(set(operations))) != tuple(operations)
        ):
            raise _error(code, "Invalid public reader operations")
    elif kind == "earn_provider":
        descriptor = _object(
            descriptor,
            frozenset({"category", "network_ids", "provider_id"}),
            code,
            "Earn provider descriptor",
        )
        descriptor["provider_id"] = _identifier(
            descriptor["provider_id"], code=code, label="Earn provider id",
        )
        if descriptor["category"] not in {"LENDING", "VAULT"}:
            raise _error(code, "Invalid Earn provider category")
        network_ids = descriptor["network_ids"]
        if (
            not isinstance(network_ids, list)
            or not network_ids
            or len(network_ids) > 16
            or any(
                not isinstance(network_id, str)
                or _ID_RE.fullmatch(network_id) is None
                for network_id in network_ids
            )
            or tuple(sorted(set(network_ids))) != tuple(network_ids)
        ):
            raise _error(code, "Invalid Earn provider networks")
    elif kind == "hermes_toolset":
        descriptor = _object(
            descriptor,
            frozenset({"descriptor_path", "skill_ids"}),
            code,
            "Hermes toolset descriptor",
        )
        validate_relative_path(descriptor["descriptor_path"], code=code)
        skill_ids = descriptor["skill_ids"]
        if (
            not isinstance(skill_ids, list)
            or any(
                not isinstance(skill_id, str)
                or not skill_id.startswith("holon-")
                or _ID_RE.fullmatch(skill_id) is None
                for skill_id in skill_ids
            )
            or tuple(sorted(set(skill_ids))) != tuple(skill_ids)
        ):
            raise _error(code, "Invalid module skill ids")
    elif kind == "wallet_page":
        descriptor = _object(
            descriptor,
            frozenset({"icon_source", "label", "qml_path", "route"}),
            code,
            "Wallet page descriptor",
        )
        icon_source = descriptor["icon_source"]
        if not isinstance(icon_source, str) or len(icon_source) > MAX_PATH_LENGTH:
            raise _error(code, "Invalid Wallet page icon")
        if icon_source:
            validate_relative_path(icon_source, code=code)
        validate_relative_path(descriptor["qml_path"], code=code)
        _string(descriptor["label"], maximum=40, code=code, label="Wallet page label")
        route = _string(
            descriptor["route"], maximum=96, code=code, label="Wallet page route",
        )
        if route != f"module:{capability_id.rsplit('.', 1)[0]}":
            raise _error(code, "Invalid Wallet page route")
    elif kind == "protected_action_adapter":
        descriptor = _object(
            descriptor,
            frozenset({"action_types"}),
            code,
            "protected action descriptor",
        )
        action_types = descriptor["action_types"]
        if not isinstance(action_types, list) or action_types:
            raise _error(code, "M7 protected action adapters cannot expose actions")
    return CapabilityDeclaration(
        capability_id, kind, version, component, entry_point, dict(descriptor),
    )


def _module_file(value: Any) -> ModuleFile:
    code = MODULE_MANIFEST_INVALID
    raw = _object(value, FILE_FIELDS, code, "module file")
    path = validate_relative_path(raw["path"], code=code)
    digest = _digest(raw["sha256"], code, "module file digest")
    targets_value = raw["targets"]
    if not isinstance(targets_value, list) or not targets_value:
        raise _error(code, "Invalid module file targets")
    targets = tuple(targets_value)
    if (
        any(not isinstance(item, str) or item not in FILE_TARGETS for item in targets)
        or tuple(sorted(set(targets))) != targets
    ):
        raise _error(code, "Invalid module file targets")
    return ModuleFile(path, digest, targets)


def encode_manifest(value: ModuleManifest) -> bytes:
    return _canonical(value.to_dict())


def decode_manifest(raw: bytes) -> ModuleManifest:
    code = MODULE_MANIFEST_INVALID
    value = _parse(raw, maximum=MAX_MANIFEST_BYTES, code=code)
    item = _object(value, MANIFEST_FIELDS, code, "manifest")
    if item["manifest_version"] != MANIFEST_VERSION:
        raise _error(code, "Unsupported manifest version")
    module_id = _identifier(item["module_id"], code=code, label="module id")
    module_version = _string(
        item["module_version"], maximum=64, code=code, label="module version",
    )
    if _SEMVER_RE.fullmatch(module_version) is None:
        raise _error(code, "Invalid module version")
    display_name = _string(
        item["display_name"], maximum=80, code=code, label="display name",
    )
    core_api_version = _api_version(item["core_api_version"], code)
    components_value = item["components"]
    if not isinstance(components_value, list):
        raise _error(code, "Invalid components")
    components = tuple(components_value)
    if (
        any(not isinstance(component, str) or component not in COMPONENTS for component in components)
        or tuple(sorted(set(components))) != components
    ):
        raise _error(code, "Invalid components")
    capabilities_value = item["capabilities"]
    files_value = item["files"]
    if (
        not isinstance(capabilities_value, list)
        or len(capabilities_value) > MAX_CAPABILITIES
        or not isinstance(files_value, list)
        or len(files_value) > MAX_MODULE_FILES
    ):
        raise _error(code, "Invalid manifest collection size")
    capabilities = tuple(_capability(entry) for entry in capabilities_value)
    files = tuple(_module_file(entry) for entry in files_value)
    capability_ids = tuple(item.capability_id for item in capabilities)
    file_paths = tuple(item.path.casefold() for item in files)
    if tuple(sorted(set(capability_ids))) != capability_ids:
        raise _error(code, "Capability ids must be unique and sorted")
    if tuple(sorted(set(file_paths))) != file_paths:
        raise _error(code, "Module file paths must be unique and sorted")
    if any(capability.component not in components for capability in capabilities):
        raise _error(code, "Capability component is undeclared")
    earn_provider_keys: list[tuple[str, str]] = []
    for capability in capabilities:
        if capability.kind != "earn_provider":
            continue
        provider_id = str(capability.descriptor["provider_id"])
        if provider_id != module_id and not provider_id.startswith(f"{module_id}."):
            raise _error(code, "Earn provider id is outside the module namespace")
        earn_provider_keys.append((capability.component, provider_id))
    if len(earn_provider_keys) != len(set(earn_provider_keys)):
        raise _error(code, "Duplicate Earn provider for component")
    for capability in capabilities:
        if capability.entry_point is None:
            continue
        module_name = capability.entry_point.split(":", 1)[0]
        source = "src/" + module_name.replace(".", "/") + ".py"
        package = "src/" + module_name.replace(".", "/") + "/__init__.py"
        if source.casefold() not in file_paths and package.casefold() not in file_paths:
            raise _error(code, "Entry point source is not integrity-covered")
        covered = next(
            file for file in files
            if file.path.casefold() in {source.casefold(), package.casefold()}
        )
        if capability.component not in covered.targets and "shared" not in covered.targets:
            raise _error(code, "Entry point source has the wrong component target")
    for capability in capabilities:
        if capability.kind == "hermes_toolset":
            descriptor_path = str(capability.descriptor["descriptor_path"])
            descriptor_file = next(
                (file for file in files if file.path == descriptor_path), None,
            )
            if descriptor_file is None or "hermes" not in descriptor_file.targets:
                raise _error(code, "Hermes descriptor is not integrity-covered")
            for skill_id in capability.descriptor["skill_ids"]:
                skill_path = f"skills/crypto/{skill_id}/SKILL.md"
                skill_file = next((file for file in files if file.path == skill_path), None)
                if skill_file is None or "skill" not in skill_file.targets:
                    raise _error(code, "Module skill is not integrity-covered")
        elif capability.kind == "wallet_page":
            qml_path = str(capability.descriptor["qml_path"])
            qml_file = next((file for file in files if file.path == qml_path), None)
            if qml_file is None or "wallet" not in qml_file.targets:
                raise _error(code, "Wallet page resource is not integrity-covered")
    manifest = ModuleManifest(
        module_id, module_version, display_name, core_api_version,
        components, capabilities, files,
    )
    if encode_manifest(manifest) != raw:
        raise _error(code, "Manifest JSON is not canonical")
    return manifest


def _catalog_entry(value: Any) -> ModuleCatalogEntry:
    code = MODULE_CATALOG_INVALID
    raw = _object(value, CATALOG_ENTRY_FIELDS, code, "catalog entry")
    module_id = _identifier(raw["module_id"], code=code, label="module id")
    if type(raw["enabled"]) is not bool:
        raise _error(code, "Invalid module enabled state")
    manifest_path = validate_relative_path(raw["manifest_path"], code=code)
    if manifest_path != f"modules/{module_id}/module-manifest.json":
        raise _error(code, "Invalid module manifest location")
    digest = _digest(raw["manifest_sha256"], code, "manifest digest")
    return ModuleCatalogEntry(module_id, raw["enabled"], manifest_path, digest)


def encode_catalog(value: ModuleCatalog) -> bytes:
    return _canonical(value.to_dict())


def decode_catalog(raw: bytes) -> ModuleCatalog:
    code = MODULE_CATALOG_INVALID
    value = _parse(raw, maximum=MAX_CATALOG_BYTES, code=code)
    item = _object(value, CATALOG_FIELDS, code, "catalog")
    if item["catalog_version"] != CATALOG_VERSION:
        raise _error(code, "Unsupported catalog version")
    composition_id = _identifier(
        item["composition_id"], code=code, label="composition id",
    )
    core_api_version = _api_version(item["core_api_version"], code)
    modules_value = item["modules"]
    if not isinstance(modules_value, list) or len(modules_value) > MAX_MODULES:
        raise _error(code, "Invalid catalog module count")
    modules = tuple(_catalog_entry(entry) for entry in modules_value)
    module_ids = tuple(entry.module_id for entry in modules)
    if tuple(sorted(set(module_ids))) != module_ids:
        duplicate_code = MODULE_DUPLICATE if len(set(module_ids)) != len(module_ids) else code
        raise _error(duplicate_code, "Catalog module ids must be unique and sorted")
    catalog = ModuleCatalog(composition_id, core_api_version, modules)
    if encode_catalog(catalog) != raw:
        raise _error(code, "Catalog JSON is not canonical")
    return catalog
