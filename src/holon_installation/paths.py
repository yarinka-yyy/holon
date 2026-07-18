"""Safe manifest path rules."""

from __future__ import annotations

import os
from pathlib import Path, PurePosixPath
import stat

from .model import ManifestError

MAX_PATH_LENGTH = 240
ROOT_FILES = frozenset({"install.ps1", "uninstall.ps1", "InstallSupport.psm1", "INSTALL.md"})


def validate_relative_path(value: str) -> str:
    if not value or len(value) > MAX_PATH_LENGTH or "\\" in value or ":" in value:
        raise ManifestError("Invalid release path")
    if any(ord(character) < 32 for character in value):
        raise ManifestError("Invalid release path")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ManifestError("Invalid release path")
    if any(part.endswith((" ", ".")) for part in path.parts) or str(path) != value:
        raise ManifestError("Invalid release path")
    return value


def expected_component(value: str) -> str:
    if value == "payload/app/HolonGuard.exe":
        return "guard"
    if value == "payload/app/HolonWallet.exe":
        return "wallet"
    if value == "payload/app/holon_policy/baseline-policy.json":
        return "policy"
    if value.startswith(("payload/plugin/holon_contracts/", "payload/plugin/holon_guard_ipc/")):
        return "contracts"
    if value.startswith("payload/plugin/"):
        return "plugin"
    if value.startswith("payload/initial-data/"):
        return "initial-data"
    if value in ROOT_FILES:
        return "installer"
    raise ManifestError("Release path is outside the fixed layout")


def resolve_package_file(root: Path, relative: str) -> Path:
    validate_relative_path(relative)
    candidate = root.joinpath(*PurePosixPath(relative).parts)
    try:
        root_value = root.resolve(strict=True)
        candidate_value = candidate.resolve(strict=True)
        candidate_value.relative_to(root_value)
        lexical_root = Path(os.path.abspath(root))
        lexical_candidate = Path(os.path.abspath(candidate))
        lexical_candidate.relative_to(lexical_root)
    except (OSError, ValueError) as exc:
        raise ManifestError("Release file is unavailable") from exc
    current = lexical_candidate
    while True:
        details = current.lstat()
        attributes = getattr(details, "st_file_attributes", 0)
        if current.is_symlink() or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0):
            raise ManifestError("Release file uses a link")
        if current == lexical_root:
            break
        current = current.parent
    if not candidate_value.is_file():
        raise ManifestError("Release file is not regular")
    return candidate_value
