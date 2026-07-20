"""Qt Quick application composition and standalone entry point."""

from __future__ import annotations

import sys
from importlib.resources import as_file, files

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QIcon
from PySide6.QtQuick import QQuickView

from .controller import WalletController
from .model import WalletShellState
from .single_instance import ProcessInstance

WINDOW_TITLE = "Holon Wallet"
MUTEX_NAME = r"Local\HolonWallet.M3.01"


class WalletApplication:
    """Owns the Qt runtime, controller, and QML-backed window."""

    def __init__(
        self,
        qt_app: QGuiApplication | None = None,
        state: WalletShellState | None = None,
    ) -> None:
        self.qt_app = qt_app or QGuiApplication.instance()
        if self.qt_app is None:
            self.qt_app = QGuiApplication(sys.argv)
        self.qt_app.setApplicationDisplayName(WINDOW_TITLE)
        self.qt_app.setApplicationName("HolonWallet")
        qml_package = files("holon_wallet.qml")
        with as_file(qml_package.joinpath("assets/holon.svg")) as icon_path:
            self.qt_app.setWindowIcon(QIcon(str(icon_path)))

        self.controller = WalletController(state)
        self.window = QQuickView()
        self.engine = self.window.engine()
        self.qml_warnings: list[str] = []
        self.engine.warnings.connect(self._record_warnings)
        context = self.engine.rootContext()
        context.setContextProperty("walletController", self.controller)
        context.setContextProperty("walletWindow", self.window)
        self.window.setTitle(WINDOW_TITLE)
        self.window.setColor(QColor("transparent"))
        self.window.setFlags(Qt.Window | Qt.FramelessWindowHint)
        self.window.setMinimumSize(QSize(430, 575))
        self.window.resize(514, 686)
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        with as_file(qml_package.joinpath("Main.qml")) as qml_path:
            self.window.setSource(QUrl.fromLocalFile(str(qml_path)))
        if self.window.status() == QQuickView.Error or self.window.rootObject() is None:
            details = "; ".join(self.qml_warnings) or "unknown QML error"
            raise RuntimeError(f"Wallet QML failed to load: {details}")
        self.window.show()

    def _record_warnings(self, warnings: list[object]) -> None:
        self.qml_warnings.extend(str(warning.toString()) for warning in warnings)

    def run(self) -> int:
        return self.qt_app.exec()

    def close(self) -> None:
        self.window.close()
        self.window.deleteLater()
        self.qt_app.processEvents()


def main() -> int:
    instance = ProcessInstance(MUTEX_NAME, WINDOW_TITLE)
    if not instance.acquire():
        return 0
    try:
        return WalletApplication().run()
    finally:
        instance.release()
