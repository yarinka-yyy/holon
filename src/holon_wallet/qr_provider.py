"""In-memory Qt Quick QR provider for public checksum addresses."""

from __future__ import annotations

from urllib.parse import unquote

import qrcode
from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from web3 import Web3


QR_PROVIDER_ID = "walletQr"
QR_BACKGROUND = QColor("#F4F7F6")
QR_FOREGROUND = QColor("#10191C")


class AddressQrProvider(QQuickImageProvider):
    """Renders an address directly to QImage; no Pillow or filesystem use."""

    def __init__(self) -> None:
        super().__init__(QQuickImageProvider.Image)

    def requestImage(
        self,
        image_id: str,
        size: QSize,
        requested_size: QSize,
    ) -> QImage:
        address = _checksum_payload(image_id)
        edge = _requested_edge(requested_size)
        image = _render_address(address, edge)
        result_size = QSize(image.width(), image.height())
        size.setWidth(result_size.width())
        size.setHeight(result_size.height())
        return image


def _checksum_payload(image_id: str) -> str:
    candidate = unquote(image_id).strip()
    if not Web3.is_address(candidate):
        raise ValueError("Invalid public address")
    return Web3.to_checksum_address(candidate)


def _requested_edge(requested_size: QSize) -> int:
    requested = max(requested_size.width(), requested_size.height())
    return max(160, min(requested if requested > 0 else 320, 1024))


def _render_address(address: str, edge: int) -> QImage:
    code = qrcode.QRCode(
        version=None,
        error_correction=qrcode.constants.ERROR_CORRECT_M,
        box_size=1,
        border=4,
    )
    code.add_data(address)
    code.make(fit=True)
    matrix = code.get_matrix()
    modules = len(matrix)
    module_size = max(1, edge // modules)
    rendered_edge = modules * module_size
    image = QImage(rendered_edge, rendered_edge, QImage.Format_ARGB32_Premultiplied)
    image.fill(QR_BACKGROUND)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, False)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QR_FOREGROUND)
    for row, values in enumerate(matrix):
        for column, enabled in enumerate(values):
            if enabled:
                painter.drawRect(
                    column * module_size,
                    row * module_size,
                    module_size,
                    module_size,
                )
    painter.end()
    return image
