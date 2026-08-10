"""Generate and verify the bundled third-party license text."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path
import platform
import re
import sys


ROOT = Path(__file__).parents[1]
MANIFEST_PATH = ROOT / "packaging" / "third-party-components.json"
OUTPUT_PATH = ROOT / "THIRD_PARTY_LICENSES.txt"
SUPPLEMENTAL = (
    ROOT / "packaging" / "licenses" / "GPL-3.0-only.txt",
    ROOT / "packaging" / "licenses" / "LGPL-3.0-only.txt",
)
LICENSE_NAME = re.compile(r"^(?:license|copying|notice|authors)(?:[._-]|$)", re.I)


class LicenseBundleError(ValueError):
    """The release license inventory is incomplete or inconsistent."""


def _normalize_legal_text(value: str) -> str:
    """Preserve legal wording while removing non-semantic line-end whitespace."""
    return "\n".join(line.rstrip() for line in value.replace("\r\n", "\n").splitlines()).rstrip()


def _canonical(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _load_manifest() -> dict:
    value = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
    if set(value) != {"schema_version", "python", "components"}:
        raise LicenseBundleError("Invalid third-party manifest fields")
    if value["schema_version"] != 1:
        raise LicenseBundleError("Unsupported third-party manifest")
    if not isinstance(value["components"], list) or not value["components"]:
        raise LicenseBundleError("Third-party components are unavailable")
    names: set[str] = set()
    for component in value["components"]:
        required = {"name", "version", "license", "scope"}
        if not isinstance(component, dict) or not required.issubset(component):
            raise LicenseBundleError("Third-party component fields are incomplete")
        if not all(isinstance(component[field], str) and component[field] for field in required):
            raise LicenseBundleError("Third-party component fields are invalid")
        name = _canonical(component["name"])
        if name in names:
            raise LicenseBundleError("Duplicate third-party component")
        names.add(name)
        if component["scope"] not in {"runtime", "build"}:
            raise LicenseBundleError(f"Invalid component scope: {component['name']}")
        if component["license"].casefold() in {"unknown", "proprietary"}:
            raise LicenseBundleError(f"Unreviewed license: {component['name']}")
        if re.match(r"^GPL", component["license"], re.I) and not component.get("gpl_exception", False):
            raise LicenseBundleError(f"GPL-only component: {component['name']}")
    return value


def _distribution(name: str) -> importlib.metadata.Distribution:
    expected = _canonical(name)
    for distribution in importlib.metadata.distributions():
        if _canonical(distribution.metadata.get("Name", "")) == expected:
            return distribution
    raise LicenseBundleError(f"Required distribution is unavailable: {name}")


def _license_files(distribution: importlib.metadata.Distribution) -> list[tuple[str, str]]:
    declared = {
        value.replace("\\", "/").casefold()
        for value in distribution.metadata.get_all("License-File") or []
    }
    selected: dict[str, str] = {}
    for entry in distribution.files or ():
        relative = str(entry).replace("\\", "/")
        name = Path(relative).name
        if not (
            relative.casefold() in declared
            or any(relative.casefold().endswith("/" + item) for item in declared)
            or LICENSE_NAME.match(name)
        ):
            continue
        path = Path(distribution.locate_file(entry))
        if not path.is_file() or path.stat().st_size > 1024 * 1024:
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
        selected[relative] = _normalize_legal_text(text)
    return sorted(selected.items(), key=lambda item: item[0].casefold())


def _section(title: str, license_name: str, files: list[tuple[str, str]]) -> list[str]:
    lines = ["=" * 79, title, f"Declared license: {license_name}", "-" * 79]
    for name, content in files:
        lines.extend((f"File: {name}", "", content, ""))
    return lines


def render() -> str:
    manifest = _load_manifest()
    python = manifest["python"]
    if platform.python_version() != python["version"]:
        raise LicenseBundleError("CPython version does not match the release manifest")
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if not python_license.is_file():
        raise LicenseBundleError("CPython license is unavailable")
    lines = [
        "HOLON THIRD-PARTY LICENSES",
        "",
        "This file covers the locked runtime dependency set and the build",
        "bootloader distributed in Holon 0.2.0-alpha.",
        "",
    ]
    lines.extend(_section(
        f"CPython {python['version']}", python["license"],
        [("LICENSE.txt", _normalize_legal_text(python_license.read_text(encoding="utf-8")))],
    ))
    for component in manifest["components"]:
        distribution = _distribution(component["name"])
        if distribution.version != component["version"]:
            raise LicenseBundleError(f"Version mismatch: {component['name']}")
        metadata_license = (
            distribution.metadata.get("License-Expression")
            or distribution.metadata.get("License")
            or ""
        ).strip()
        if (
            re.match(r"^GPL", metadata_license, re.I)
            and not component.get("gpl_exception", False)
        ):
            raise LicenseBundleError(f"GPL-only component: {component['name']}")
        collect = component.get("collect_license_files", True)
        files = _license_files(distribution) if collect else []
        if collect and not files:
            raise LicenseBundleError(f"License text is unavailable: {component['name']}")
        if not collect:
            evidence = component.get("evidence")
            note = (
                f"Reviewed license evidence: {evidence}"
                if evidence else "Covered by the Qt license texts below."
            )
            files = [("reviewed-license-evidence.txt", note)]
        title = f"{distribution.metadata['Name']} {distribution.version} ({component['scope']})"
        lines.extend(_section(title, component["license"], files))
    lines.extend(("=" * 79, "Qt 6.11.1, PySide6 6.11.1, and Shiboken6 6.11.1", ""))
    for path in SUPPLEMENTAL:
        if not path.is_file():
            raise LicenseBundleError(f"Supplemental license is unavailable: {path.name}")
        lines.extend((
            f"File: {path.name}", "",
            _normalize_legal_text(path.read_text(encoding="utf-8")), "",
        ))
    return "\n".join(lines).rstrip() + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--write", action="store_true")
    parser.add_argument("--output", type=Path, default=OUTPUT_PATH)
    arguments = parser.parse_args()
    rendered = render()
    if arguments.check and arguments.write:
        raise SystemExit("Choose either --check or --write")
    if arguments.check:
        if not arguments.output.is_file() or arguments.output.read_text(encoding="utf-8") != rendered:
            raise SystemExit("THIRD_PARTY_LICENSES.txt is not current")
        print("third_party_licenses=ok")
        return
    if arguments.write:
        arguments.output.write_text(rendered, encoding="utf-8", newline="\n")
        print(f"third_party_license_bytes={len(rendered.encode('utf-8'))}")
        return
    print(rendered, end="")


if __name__ == "__main__":
    main()
