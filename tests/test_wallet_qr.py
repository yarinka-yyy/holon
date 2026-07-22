from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize
from web3 import Web3

from holon_wallet.qr_provider import AddressQrProvider, _checksum_payload


ADDRESS = "0x1234567890abcdef1234567890abcdef12345678"


def test_qr_payload_is_checksum_address() -> None:
    assert _checksum_payload(ADDRESS) == Web3.to_checksum_address(ADDRESS)


def test_qr_is_rendered_in_memory_without_files(tmp_path: Path) -> None:
    before = tuple(tmp_path.iterdir())
    provider = AddressQrProvider()
    size = QSize()
    image = provider.requestImage(ADDRESS, size, QSize(320, 320))

    assert image.width() == image.height()
    assert image.width() <= 320
    assert size == QSize(image.width(), image.height())
    assert tuple(tmp_path.iterdir()) == before


def test_invalid_address_is_rejected() -> None:
    try:
        _checksum_payload("not-an-address")
    except ValueError as error:
        assert str(error) == "Invalid public address"
    else:
        raise AssertionError("Invalid QR payload was accepted")
