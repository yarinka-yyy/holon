from __future__ import annotations

import ast
import unittest
from pathlib import Path


SOURCE_ROOT = Path(__file__).parents[1] / "src"
COMPATIBLE_PACKAGES = ("holon_contracts", "holon_guard_ipc", "holon_hermes_plugin")
ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "ctypes",
    "dataclasses",
    "datetime",
    "enum",
    "holon_contracts",
    "holon_guard_ipc",
    "json",
    "multiprocessing",
    "re",
    "subprocess",
    "sys",
    "time",
    "typing",
    "uuid",
}


def compatible_sources() -> list[Path]:
    return [
        source_path
        for package in COMPATIBLE_PACKAGES
        for source_path in (SOURCE_ROOT / package).glob("*.py")
    ]


class Python311CompatibilityTests(unittest.TestCase):
    def test_plugin_sources_parse_as_python311(self) -> None:
        for source_path in compatible_sources():
            source = source_path.read_text(encoding="utf-8")
            ast.parse(source, filename=str(source_path), feature_version=(3, 11))

    def test_plugin_imports_only_python_standard_library_and_itself(self) -> None:
        imported_roots: set[str] = set()
        for source_path in compatible_sources():
            tree = ast.parse(source_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported_roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    imported_roots.add(node.module.split(".")[0])
        self.assertLessEqual(imported_roots, ALLOWED_IMPORT_ROOTS)


if __name__ == "__main__":
    unittest.main()
