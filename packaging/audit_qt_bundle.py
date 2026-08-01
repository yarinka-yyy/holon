"""Audit bundled Qt modules against the pinned LGPL source set."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
from pathlib import Path


ROOT = Path(__file__).parents[1]
SOURCE_MANIFEST = ROOT / "packaging" / "qt-source-assets.json"
QTBASE_DLLS = {
    "Qt6Core.dll", "Qt6Gui.dll", "Qt6Network.dll", "Qt6OpenGL.dll",
    "Qt6Sql.dll", "Qt6Test.dll", "Qt6Widgets.dll",
}
QTBASE_PLUGINS = {
    "generic/qtuiotouchplugin.dll",
    "imageformats/qgif.dll", "imageformats/qico.dll", "imageformats/qjpeg.dll",
    "networkinformation/qnetworklistmanager.dll",
    "platforms/qdirect2d.dll", "platforms/qminimal.dll",
    "platforms/qoffscreen.dll", "platforms/qwindows.dll",
    "tls/qcertonlybackend.dll", "tls/qopensslbackend.dll", "tls/qschannelbackend.dll",
}
QTDECLARATIVE_PLUGIN_PREFIX = "qmltooling/"
QTIMAGEFORMAT_PLUGINS = {
    "imageformats/qicns.dll", "imageformats/qtga.dll", "imageformats/qtiff.dll",
    "imageformats/qwbmp.dll", "imageformats/qwebp.dll",
}
QTSVG_PLUGINS = {"iconengines/qsvgicon.dll", "imageformats/qsvg.dll"}
EXPECTED_MODULES = {"qtbase", "qtdeclarative", "qtsvg", "qtimageformats", "qtquicktimeline"}


class QtBundleError(ValueError):
    """The bundled Qt module set is not covered by reviewed sources."""


def classify_qt_entry(value: str) -> str | None:
    name = value.replace("\\", "/")
    if name.startswith("PySide6/qml/"):
        return "qtquicktimeline" if "/Timeline/" in name else "qtdeclarative"
    if name.startswith("PySide6/plugins/"):
        relative = name.removeprefix("PySide6/plugins/")
        if relative in QTBASE_PLUGINS:
            return "qtbase"
        if relative.startswith(QTDECLARATIVE_PLUGIN_PREFIX):
            return "qtdeclarative"
        if relative in QTIMAGEFORMAT_PLUGINS:
            return "qtimageformats"
        if relative in QTSVG_PLUGINS:
            return "qtsvg"
        raise QtBundleError(f"Unreviewed Qt plugin: {relative}")
    if not name.startswith("PySide6/Qt6") or not name.endswith(".dll"):
        return None
    dll = name.rsplit("/", 1)[-1]
    if dll in QTBASE_DLLS:
        return "qtbase"
    if dll.startswith("Qt6Svg"):
        return "qtsvg"
    if dll.startswith("Qt6QuickTimeline"):
        return "qtquicktimeline"
    if dll.startswith(("Qt6Labs", "Qt6Qml", "Qt6Quick")):
        return "qtdeclarative"
    raise QtBundleError(f"Unreviewed Qt library: {dll}")


def audit(executable: Path) -> tuple[int, set[str]]:
    if not executable.is_file():
        raise QtBundleError("Wallet executable is unavailable")
    try:
        from PyInstaller.archive.readers import CArchiveReader
    except ImportError as exc:
        raise QtBundleError("PyInstaller archive reader is unavailable") from exc
    entries = CArchiveReader(str(executable)).toc
    modules = {module for name in entries if (module := classify_qt_entry(name))}
    if modules != EXPECTED_MODULES:
        raise QtBundleError(f"Qt source coverage mismatch: {sorted(modules)}")
    manifest = json.loads(SOURCE_MANIFEST.read_text(encoding="utf-8"))
    covered = {item for asset in manifest["assets"] for item in asset["covers"]}
    if not modules.issubset(covered):
        raise QtBundleError("Qt source manifest is incomplete")
    if importlib.metadata.version("pyside6-essentials") != manifest["pyside_version"]:
        raise QtBundleError("PySide6 version mismatch")
    if importlib.metadata.version("shiboken6") != manifest["pyside_version"]:
        raise QtBundleError("Shiboken6 version mismatch")
    return len(entries), modules


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("wallet", type=Path)
    arguments = parser.parse_args()
    count, modules = audit(arguments.wallet.resolve())
    print(f"archive_entries={count}")
    print(f"qt_source_modules={','.join(sorted(modules))}")


if __name__ == "__main__":
    main()
