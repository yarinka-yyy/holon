"""Canonical release-manifest codec."""

from __future__ import annotations

import json
from pathlib import Path
import stat
from typing import Any, Mapping

from .model import (
    COMPONENT_VERSIONS, HERMES_COMPATIBILITY, MANIFEST_FIELDS, MANIFEST_VERSION, PACKAGE_VERSION,
    VERSION_FIELDS, ManifestError, ReleaseFile, ReleaseManifest,
)
from .paths import expected_component, validate_relative_path

MAX_MANIFEST_BYTES = 256 * 1024
MAX_RELEASE_FILES = 4096


def _strict_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value = dict(pairs)
    if len(value) != len(pairs):
        raise ManifestError("Release manifest contains duplicate fields")
    return value


def encode_manifest(manifest: ReleaseManifest) -> bytes:
    return (json.dumps(
        manifest.to_dict(), ensure_ascii=True, separators=(",", ":"), sort_keys=True,
    ) + "\n").encode("utf-8")


def decode_manifest(raw: bytes) -> ReleaseManifest:
    if not raw or len(raw) > MAX_MANIFEST_BYTES:
        raise ManifestError("Release manifest size is invalid")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object)
    except (UnicodeDecodeError, json.JSONDecodeError, ManifestError) as exc:
        raise ManifestError("Release manifest is invalid") from exc
    return parse_manifest(value)


def parse_manifest(value: Mapping[str, Any]) -> ReleaseManifest:
    if not isinstance(value, Mapping) or set(value) != MANIFEST_FIELDS:
        raise ManifestError("Invalid release manifest fields")
    if value.get("manifest_version") != MANIFEST_VERSION:
        raise ManifestError("Unsupported release manifest version")
    if value.get("package_version") != PACKAGE_VERSION:
        raise ManifestError("Unsupported package version")
    if value.get("hermes_compatibility") != HERMES_COMPATIBILITY:
        raise ManifestError("Unsupported Hermes compatibility")
    versions = value.get("component_versions")
    if not isinstance(versions, Mapping) or set(versions) != VERSION_FIELDS:
        raise ManifestError("Invalid component versions")
    if any(not isinstance(item, str) or not item or len(item) > 32 for item in versions.values()):
        raise ManifestError("Invalid component version")
    if dict(versions) != COMPONENT_VERSIONS:
        raise ManifestError("Incompatible component version")
    raw_files = value.get("files")
    if not isinstance(raw_files, list) or not raw_files or len(raw_files) > MAX_RELEASE_FILES:
        raise ManifestError("Invalid release files")
    files = tuple(ReleaseFile.from_dict(item) for item in raw_files)
    paths: list[str] = []
    for item in files:
        path = validate_relative_path(item.path)
        if item.component != expected_component(path):
            raise ManifestError("Release component does not match its path")
        expected_critical = path.startswith(("payload/app/", "payload/plugin/"))
        if item.critical is not expected_critical:
            raise ManifestError("Release critical marker does not match its path")
        paths.append(path.casefold())
    if len(set(paths)) != len(paths):
        raise ManifestError("Duplicate release path")
    if paths != sorted(paths):
        raise ManifestError("Release files are not canonical")
    return ReleaseManifest(PACKAGE_VERSION, dict(versions), files)


def load_manifest(path: Path) -> ReleaseManifest:
    try:
        details = path.lstat()
        attributes = getattr(details, "st_file_attributes", 0)
        if path.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ManifestError("Release manifest uses a link")
        return decode_manifest(path.read_bytes())
    except OSError as exc:
        raise ManifestError("Release manifest is unavailable") from exc
