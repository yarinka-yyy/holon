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

from .codec import encode_manifest
from .model import COMPONENT_VERSIONS, PACKAGE_VERSION, ReleaseFile, ReleaseManifest


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
    if relative.startswith("payload/app/holon_policy/"):
        return "policy"
    if relative.startswith("payload/plugin/holon_contracts/"):
        return "contracts"
    if relative.startswith("payload/plugin/holon_guard_ipc/"):
        return "contracts"
    if relative.startswith("payload/plugin/"):
        return "plugin"
    if relative.startswith("payload/skills/"):
        return "skills"
    if relative.startswith("payload/initial-data/"):
        return "initial-data"
    return "installer"


class PackageBuilder:
    def __init__(self, source_root: Path) -> None:
        self.source_root = source_root

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

    def _copy_skills(self, root: Path) -> None:
        skills_root = self.source_root / "skills" / "crypto"
        skill_files = {path.parent.name: path for path in skills_root.glob("*/SKILL.md")}
        if set(skill_files) != {"holon", "holon-lending"}:
            raise BuildError("Holon skill source set is invalid")
        for name in sorted(skill_files):
            self._copy_file(
                skill_files[name],
                root / "payload" / "skills" / "crypto" / name / "SKILL.md",
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
            for kind, source in artifacts.items():
                self._validate_production_artifact(kind, source)
            if artifacts["guard"].resolve() == artifacts["wallet"].resolve():
                raise BuildError("Guard and Wallet artifacts must be distinct")
        if destination.exists() and any(destination.iterdir()):
            raise BuildError("Staging destination must be empty")
        destination.mkdir(parents=True, exist_ok=True)
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
        self._copy_plugin(destination)
        self._copy_skills(destination)
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
