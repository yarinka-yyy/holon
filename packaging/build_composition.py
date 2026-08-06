from __future__ import annotations

import argparse
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--destination", type=Path, required=True)
    parser.add_argument("--composition-id", required=True)
    parser.add_argument("--module-root", type=Path, action="append", default=[])
    parser.add_argument("--disabled-module-id", action="append", default=[])
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from holon_modules import build_composition

    build_composition(
        args.destination,
        args.composition_id,
        args.module_root,
        disabled_module_ids=args.disabled_module_id,
    )
    print(args.destination / "module-catalog.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
