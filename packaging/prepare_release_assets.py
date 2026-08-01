"""Prepare the fixed v0.1.0-alpha GitHub Release asset set."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import ssl
import urllib.request

import certifi


ROOT = Path(__file__).parents[1]
MANIFEST = ROOT / "packaging" / "qt-source-assets.json"
SETUP_NAME = "Holon-0.1.0-alpha-Setup.exe"
CHECKSUM_NAME = "SHA256SUMS.txt"
TLS_CONTEXT = ssl.create_default_context(cafile=certifi.where())


class ReleaseAssetError(ValueError):
    """The release asset set is incomplete or unexpected."""


def _digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def _validate(path: Path, size: int, digest: str) -> bool:
    return path.is_file() and path.stat().st_size == size and _digest(path) == digest


def _download(url: str, destination: Path, size: int, digest: str) -> None:
    if not url.startswith("https://download.qt.io/"):
        raise ReleaseAssetError("Unapproved source host")
    if _validate(destination, size, digest):
        return
    temporary = destination.with_name(destination.name + ".part")
    temporary.unlink(missing_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "Holon-release-builder"})
    try:
        with urllib.request.urlopen(
            request, timeout=60, context=TLS_CONTEXT,
        ) as response, temporary.open("wb") as output:
            shutil.copyfileobj(response, output, length=1024 * 1024)
        if not _validate(temporary, size, digest):
            raise ReleaseAssetError(f"Source archive verification failed: {destination.name}")
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)


def prepare(setup: Path, destination: Path) -> list[tuple[str, int, str]]:
    if setup.name != SETUP_NAME or not setup.is_file():
        raise ReleaseAssetError("Final Setup is unavailable")
    destination.mkdir(parents=True, exist_ok=True)
    value = json.loads(MANIFEST.read_text(encoding="utf-8"))
    target_setup = destination / SETUP_NAME
    if setup.resolve() != target_setup.resolve():
        shutil.copy2(setup, target_setup)
    results = [(SETUP_NAME, target_setup.stat().st_size, _digest(target_setup))]
    for asset in value["assets"]:
        target = destination / asset["name"]
        _download(asset["url"], target, asset["bytes"], asset["sha256"])
        results.append((asset["name"], asset["bytes"], asset["sha256"]))
    checksum = destination / CHECKSUM_NAME
    checksum.write_text(
        "".join(f"{digest}  {name}\n" for name, _, digest in results),
        encoding="ascii", newline="\n",
    )
    expected = {SETUP_NAME, CHECKSUM_NAME, *(asset["name"] for asset in value["assets"])}
    actual = {path.name for path in destination.iterdir()}
    if actual != expected:
        raise ReleaseAssetError(f"Unexpected release asset set: {sorted(actual ^ expected)}")
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--setup", required=True, type=Path)
    parser.add_argument("--destination", required=True, type=Path)
    arguments = parser.parse_args()
    for name, size, digest in prepare(arguments.setup.resolve(), arguments.destination.resolve()):
        print(f"{name}|{size}|{digest}")


if __name__ == "__main__":
    main()
