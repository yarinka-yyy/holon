"""Strict release-manifest model."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

MANIFEST_VERSION = "1"
PACKAGE_VERSION = "0.1.0a0"
HERMES_COMPATIBILITY = ">=0.18.2,<0.19.0"
VERSION_FIELDS = frozenset({"plugin", "guard", "wallet", "contracts", "policy"})
COMPONENT_VERSIONS = {
    "plugin": PACKAGE_VERSION, "guard": PACKAGE_VERSION, "wallet": PACKAGE_VERSION,
    "contracts": "1", "policy": "1",
}
MANIFEST_FIELDS = frozenset(
    {"manifest_version", "package_version", "component_versions", "hermes_compatibility", "files"}
)
FILE_FIELDS = frozenset({"component", "path", "sha256", "critical"})
COMPONENTS = frozenset({"installer", "guard", "wallet", "plugin", "contracts", "policy", "initial-data"})


class ManifestError(ValueError):
    """A release manifest is unsafe or incompatible."""


@dataclass(frozen=True, slots=True)
class ReleaseFile:
    component: str
    path: str
    sha256: str
    critical: bool

    @classmethod
    def from_dict(cls, value: Mapping[str, Any]) -> "ReleaseFile":
        if not isinstance(value, Mapping) or set(value) != FILE_FIELDS:
            raise ManifestError("Invalid release file fields")
        component, path = value.get("component"), value.get("path")
        digest, critical = value.get("sha256"), value.get("critical")
        if not isinstance(component, str) or component not in COMPONENTS:
            raise ManifestError("Invalid release component")
        if not isinstance(path, str):
            raise ManifestError("Invalid release path")
        if not isinstance(digest, str) or len(digest) != 64:
            raise ManifestError("Invalid release digest")
        if any(character not in "0123456789abcdef" for character in digest):
            raise ManifestError("Invalid release digest")
        if type(critical) is not bool:
            raise ManifestError("Invalid critical marker")
        return cls(component, path, digest, critical)

    def to_dict(self) -> dict[str, object]:
        return {
            "component": self.component, "path": self.path,
            "sha256": self.sha256, "critical": self.critical,
        }


@dataclass(frozen=True, slots=True)
class ReleaseManifest:
    package_version: str
    component_versions: Mapping[str, str]
    files: tuple[ReleaseFile, ...]
    manifest_version: str = MANIFEST_VERSION
    hermes_compatibility: str = HERMES_COMPATIBILITY

    def to_dict(self) -> dict[str, object]:
        return {
            "manifest_version": self.manifest_version,
            "package_version": self.package_version,
            "component_versions": dict(self.component_versions),
            "hermes_compatibility": self.hermes_compatibility,
            "files": [item.to_dict() for item in self.files],
        }
