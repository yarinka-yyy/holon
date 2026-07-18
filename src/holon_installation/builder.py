"""Deterministic staging builder; real binaries are supplied externally."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
from typing import Mapping

from .codec import encode_manifest
from .model import COMPONENT_VERSIONS, PACKAGE_VERSION, ReleaseFile, ReleaseManifest


class BuildError(ValueError):
    pass


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
    if relative.startswith("payload/app/holon_policy/"):
        return "policy"
    if relative.startswith("payload/plugin/holon_contracts/"):
        return "contracts"
    if relative.startswith("payload/plugin/holon_guard_ipc/"):
        return "contracts"
    if relative.startswith("payload/plugin/"):
        return "plugin"
    if relative.startswith("payload/initial-data/"):
        return "initial-data"
    return "installer"


class PackageBuilder:
    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root

    @staticmethod
    def _copy_file(source: Path, target: Path) -> None:
        if not source.is_file() or source.is_symlink():
            raise BuildError("Required package artifact is unavailable")
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)

    def _copy_plugin(self, root: Path) -> None:
        plugin = self.source_root / "src" / "holon_hermes_plugin"
        for source in sorted(plugin.glob("*.py")) + [plugin / "plugin.yaml"]:
            self._copy_file(source, root / "payload" / "plugin" / source.name)
        for package_name in ("holon_contracts", "holon_guard_ipc"):
            package = self.source_root / "src" / package_name
            for source in sorted(package.glob("*.py")):
                self._copy_file(
                    source, root / "payload" / "plugin" / package_name / source.name,
                )

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
            raise BuildError("Production binaries are unavailable until Wallet packaging")
        if destination.exists() and any(destination.iterdir()):
            raise BuildError("Staging destination must be empty")
        destination.mkdir(parents=True, exist_ok=True)
        for script in ("install.ps1", "uninstall.ps1", "InstallSupport.psm1", "INSTALL.md"):
            self._copy_file(self.source_root / "packaging" / script, destination / script)
        self._copy_file(artifacts["guard"], destination / "payload" / "app" / "HolonGuard.exe")
        self._copy_file(artifacts["wallet"], destination / "payload" / "app" / "HolonWallet.exe")
        self._copy_file(
            self.source_root / "src" / "holon_policy" / "baseline-policy.json",
            destination / "payload" / "app" / "holon_policy" / "baseline-policy.json",
        )
        self._copy_plugin(destination)
        self._initial_data(destination)
        files: list[ReleaseFile] = []
        for path in sorted(item for item in destination.rglob("*") if item.is_file()):
            relative = path.relative_to(destination).as_posix()
            component = _component(relative)
            critical = relative.startswith(("payload/app/", "payload/plugin/"))
            files.append(ReleaseFile(component, relative, hashlib.sha256(path.read_bytes()).hexdigest(), critical))
        files.sort(key=lambda item: item.path.casefold())
        manifest = ReleaseManifest(PACKAGE_VERSION, COMPONENT_VERSIONS, tuple(files))
        (destination / "release-manifest.json").write_bytes(encode_manifest(manifest))
        from .verify import verify_package
        if not verify_package(destination).ok:
            raise BuildError("Built staging failed integrity verification")
        return manifest
