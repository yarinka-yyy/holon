"""Build a production Holon staging directory from packaged binaries."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-root", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    parser.add_argument("--guard", required=True, type=Path)
    parser.add_argument("--wallet", required=True, type=Path)
    parser.add_argument("--composition-root", type=Path)
    arguments = parser.parse_args()
    source_root = arguments.source_root.resolve()
    sys.path.insert(0, str(source_root / "src"))
    from holon_installation import PackageBuilder

    composition_root = (
        arguments.composition_root.resolve()
        if arguments.composition_root is not None else None
    )
    manifest = PackageBuilder(source_root, composition_root).build(
        arguments.destination.resolve(),
        {"guard": arguments.guard.resolve(), "wallet": arguments.wallet.resolve()},
    )
    print(f"package_version={manifest.package_version}")
    print(f"files={len(manifest.files)}")


if __name__ == "__main__":
    main()
