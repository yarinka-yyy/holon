"""Create a multi-resolution Windows ICO from the repository Holon SVG."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import struct


ICON_SIZES = (16, 20, 24, 32, 40, 48, 64, 128, 256)


def build_icon(source: Path, destination: Path) -> None:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QBuffer, QIODevice, QRectF
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    application = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(str(source))
    if not renderer.isValid():
        raise ValueError("Holon SVG is invalid")
    frames: list[tuple[int, bytes]] = []
    for size in ICON_SIZES:
        image = QImage(size, size, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        renderer.render(painter, QRectF(0, 0, size, size))
        painter.end()

        buffer = QBuffer()
        if (
            not buffer.open(QIODevice.OpenModeFlag.WriteOnly)
            or not image.save(buffer, "PNG")
        ):
            raise OSError("Could not encode a Holon icon frame")
        frames.append((size, bytes(buffer.data())))

    destination.parent.mkdir(parents=True, exist_ok=True)
    header = struct.pack("<HHH", 0, 1, len(frames))
    offset = 6 + 16 * len(frames)
    entries = bytearray()
    payload = bytearray()
    for size, png in frames:
        encoded_size = 0 if size == 256 else size
        entries.extend(struct.pack(
            "<BBBBHHII",
            encoded_size, encoded_size, 0, 0, 1, 32, len(png), offset,
        ))
        payload.extend(png)
        offset += len(png)
    destination.write_bytes(header + entries + payload)
    del application


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    arguments = parser.parse_args()
    build_icon(arguments.source.resolve(), arguments.destination.resolve())
    print(arguments.destination.resolve())


if __name__ == "__main__":
    main()
