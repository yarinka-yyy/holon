from __future__ import annotations

from importlib.resources import as_file, files
from xml.etree import ElementTree

from PySide6.QtGui import QImage


ASSETS = files("holon_wallet.qml").joinpath("assets")


def test_user_approved_raster_logos_are_transparent_package_resources() -> None:
    for name in ("base.png", "usdc.png"):
        with as_file(ASSETS.joinpath(name)) as path:
            image = QImage(str(path))
        assert not image.isNull()
        assert image.hasAlphaChannel()
        assert image.pixelColor(0, 0).alpha() == 0


def test_ethereum_logo_is_clean_vector_without_embedded_checkerboard() -> None:
    source = ASSETS.joinpath("ethereum.svg").read_text(encoding="utf-8")
    root = ElementTree.fromstring(source)

    assert root.tag.endswith("svg")
    assert len(list(root)) == 6
    assert "<image" not in source
    assert "#FFFFFF" not in source.upper()


def test_replaced_logo_assets_are_not_retained() -> None:
    assert not ASSETS.joinpath("base.svg").is_file()
    assert not ASSETS.joinpath("usdc.svg").is_file()
    assert not ASSETS.joinpath("ethereum-coin.svg").is_file()
