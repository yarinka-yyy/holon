"""Package and installed-runtime integrity verification."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from pathlib import Path
import re

from .codec import load_manifest
from .model import ManifestError, ReleaseFile
from .paths import resolve_package_file

SAFE_CODES = {
    "guard": "GUARD_INTEGRITY_FAILED", "wallet": "WALLET_INTEGRITY_FAILED",
    "plugin": "PLUGIN_INTEGRITY_FAILED", "contracts": "CONTRACT_INTEGRITY_FAILED",
    "policy": "POLICY_INTEGRITY_FAILED", "installer": "INSTALLER_INTEGRITY_FAILED",
    "initial-data": "INITIAL_DATA_INTEGRITY_FAILED",
}
VERSION_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


@dataclass(frozen=True, slots=True)
class IntegrityResult:
    ok: bool
    code: str
    component: str | None = None


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(65536), b""):
            value.update(block)
    return value.hexdigest()


def _verify_file(root: Path, item: ReleaseFile) -> IntegrityResult:
    try:
        path = resolve_package_file(root, item.path)
        valid = _digest(path) == item.sha256
    except (ManifestError, OSError):
        valid = False
    code = "INTEGRITY_OK" if valid else SAFE_CODES[item.component]
    return IntegrityResult(valid, code, None if valid else item.component)


def verify_package(root: Path) -> IntegrityResult:
    try:
        parsed = load_manifest(root / "release-manifest.json")
    except ManifestError:
        return IntegrityResult(False, "PACKAGE_MANIFEST_INVALID")
    for item in parsed.files:
        result = _verify_file(root, item)
        if not result.ok:
            return result
    return IntegrityResult(True, "INTEGRITY_OK")


def hermes_supported(version: str) -> bool:
    match = VERSION_RE.fullmatch(version)
    if not match:
        return False
    parts = tuple(map(int, match.groups()))
    return version == ".".join(map(str, parts)) and (0, 18, 2) <= parts < (0, 19, 0)


def verify_installed(
    manifest_path: Path, app_root: Path, plugin_root: Path, hermes_version: str,
) -> IntegrityResult:
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError:
        return IntegrityResult(False, "PACKAGE_MANIFEST_INVALID")
    if not hermes_supported(hermes_version):
        return IntegrityResult(False, "HERMES_VERSION_UNSUPPORTED")
    for item in manifest.files:
        if not item.critical or not item.path.startswith(("payload/app/", "payload/plugin/")):
            continue
        prefix = "payload/app/" if item.path.startswith("payload/app/") else "payload/plugin/"
        root = app_root if prefix == "payload/app/" else plugin_root
        installed = ReleaseFile(item.component, item.path[len(prefix):], item.sha256, True)
        result = _verify_file(root, installed)
        if not result.ok:
            return result
    return IntegrityResult(True, "INTEGRITY_OK")
