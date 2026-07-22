"""Python-painted recovery material that is never exposed as a QML property."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtQuick import QQuickPaintedItem

from .recovery import RecoveryMaterialKind


class RecoverySecretDisplay(QQuickPaintedItem):
    """Hold and paint one short-lived secret without a Qt-readable text property."""

    def __init__(self, parent=None) -> None:  # type: ignore[no-untyped-def]
        super().__init__(parent)
        self._buffer = bytearray()
        self._kind: RecoveryMaterialKind | None = None
        self._font_family = "Inter"
        self.setAntialiasing(True)

    def set_font_family(self, family: str) -> None:
        self._font_family = family
        self.update()

    def set_material(self, kind: RecoveryMaterialKind, value: str) -> None:
        self.clear_material()
        self._buffer = bytearray(value.encode("utf-8"))
        self._kind = kind
        self.update()

    def has_material(self) -> bool:
        return bool(self._buffer) and self._kind is not None

    def copy_text(self) -> str | None:
        if not self.has_material():
            return None
        return self._buffer.decode("utf-8")

    def clear_material(self) -> None:
        for index in range(len(self._buffer)):
            self._buffer[index] = 0
        self._buffer.clear()
        self._kind = None
        self.update()

    def paint(self, painter: QPainter) -> None:
        if not self.has_material():
            return
        value = self._buffer.decode("utf-8")
        try:
            if self._kind is RecoveryMaterialKind.SEED_PHRASE:
                self._paint_seed(painter, value)
            else:
                self._paint_private_key(painter, value)
        finally:
            del value

    def _paint_seed(self, painter: QPainter, value: str) -> None:
        words = value.split(" ")
        try:
            columns = 3
            rows = (len(words) + columns - 1) // columns
            gap = 8.0
            cell_width = (self.width() - gap * (columns - 1)) / columns
            cell_height = min(58.0, (self.height() - gap * (rows - 1)) / rows)
            total_height = rows * cell_height + (rows - 1) * gap
            top = max(0.0, (self.height() - total_height) / 2)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#182128"))
            for index, word in enumerate(words):
                column = index % columns
                row = index // columns
                rect = QRectF(
                    column * (cell_width + gap),
                    top + row * (cell_height + gap),
                    cell_width,
                    cell_height,
                )
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#182128"))
                painter.drawRoundedRect(rect, 11, 11)
                number_font = QFont(self._font_family, 10)
                number_font.setWeight(QFont.Weight.Normal)
                painter.setFont(number_font)
                painter.setPen(QColor("#727C84"))
                painter.drawText(
                    QRectF(rect.x() + 10, rect.y(), 24, rect.height()),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    f"{index + 1}.",
                )
                word_font = QFont(self._font_family, 11)
                word_font.setWeight(QFont.Weight.Medium)
                painter.setFont(word_font)
                painter.setPen(QColor("#F2F3F1"))
                painter.drawText(
                    QRectF(rect.x() + 34, rect.y(), rect.width() - 40, rect.height()),
                    Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft,
                    word,
                )
        finally:
            for index in range(len(words)):
                words[index] = ""
            del words

    def _paint_private_key(self, painter: QPainter, value: str) -> None:
        font = QFont(self._font_family, 13)
        font.setWeight(QFont.Weight.Medium)
        painter.setFont(font)
        painter.setPen(QColor("#F2F3F1"))
        chunks = [value[index:index + 18] for index in range(0, len(value), 18)]
        try:
            painter.drawText(
                QRectF(18, 18, self.width() - 36, self.height() - 36),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                "\n".join(chunks),
            )
        finally:
            for index in range(len(chunks)):
                chunks[index] = ""
            del chunks
