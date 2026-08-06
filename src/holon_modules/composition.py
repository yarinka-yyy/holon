"""Build a deterministic module composition from explicit source roots."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
import shutil
import stat
from typing import Iterable

from .codec import decode_catalog, decode_manifest, encode_catalog
from .model import (
    CORE_API_VERSION,
    MODULE_DUPLICATE,
    MODULE_MANIFEST_INVALID,
    ModuleCatalog,
    ModuleCatalogEntry,
    ModuleContractError,
    ModuleManifest,
)
from .runtime import _safe_file, verify_manifest_files


def _read_manifest(root: Path) -> tuple[ModuleManifest, bytes]:
    path = _safe_file(root, "module-manifest.json")
    raw = path.read_bytes()
    manifest = decode_manifest(raw)
    verify_manifest_files(root, manifest)
    return manifest, raw


def build_composition(
    destination: Path,
    composition_id: str,
    module_roots: Iterable[Path] = (),
    *,
    disabled_module_ids: Iterable[str] = (),
) -> ModuleCatalog:
    decode_catalog(encode_catalog(ModuleCatalog(composition_id, CORE_API_VERSION, ())))
    roots = tuple(Path(root) for root in module_roots)
    disabled = frozenset(disabled_module_ids)
    loaded: list[tuple[Path, ModuleManifest, bytes]] = []
    seen: set[str] = set()
    for root in roots:
        manifest, raw = _read_manifest(root)
        if manifest.module_id in seen:
            raise ModuleContractError(MODULE_DUPLICATE, "Duplicate module source root")
        seen.add(manifest.module_id)
        loaded.append((root, manifest, raw))
    if not disabled.issubset(seen):
        raise ModuleContractError(
            MODULE_MANIFEST_INVALID, "Disabled module id is not part of the composition",
        )
    if destination.exists():
        details = destination.lstat()
        attributes = getattr(details, "st_file_attributes", 0)
        if (
            not destination.is_dir()
            or destination.is_symlink()
            or attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            or any(destination.iterdir())
        ):
            raise ModuleContractError(
                MODULE_MANIFEST_INVALID, "Composition destination must be an empty directory",
            )
    lexical_destination = Path(os.path.abspath(destination))
    if lexical_destination.parent == lexical_destination:
        raise ModuleContractError(MODULE_MANIFEST_INVALID, "Unsafe composition destination")
    destination.mkdir(parents=True, exist_ok=True)
    entries: list[ModuleCatalogEntry] = []
    for root, manifest, raw in sorted(loaded, key=lambda item: item[1].module_id):
        target_root = destination / "modules" / manifest.module_id
        target_root.mkdir(parents=True, exist_ok=True)
        (target_root / "module-manifest.json").write_bytes(raw)
        for item in manifest.files:
            source = _safe_file(root, item.path)
            target = target_root.joinpath(*item.path.split("/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
        entries.append(ModuleCatalogEntry(
            manifest.module_id,
            manifest.module_id not in disabled,
            f"modules/{manifest.module_id}/module-manifest.json",
            hashlib.sha256(raw).hexdigest(),
        ))
    catalog = ModuleCatalog(composition_id, CORE_API_VERSION, tuple(entries))
    catalog_raw = encode_catalog(catalog)
    decode_catalog(catalog_raw)
    (destination / "module-catalog.json").write_bytes(catalog_raw)
    return catalog
