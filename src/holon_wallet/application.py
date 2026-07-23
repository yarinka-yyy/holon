"""Qt Quick application composition and standalone entry point."""

from __future__ import annotations

import sys
from concurrent.futures import Executor
from importlib.resources import as_file, files

from PySide6.QtCore import QSize, Qt, QUrl
from PySide6.QtGui import QColor, QCloseEvent, QFont, QFontDatabase, QGuiApplication, QIcon
from PySide6.QtQml import qmlRegisterType
from PySide6.QtQuick import QQuickView

from .approval import AllowanceReadService, RevokePreflightService
from .broadcast import (
    BroadcastReceiptTracker,
    MainnetTransferExecutor,
)
from .controller import WalletController
from .history import HistoryStore
from .public_data import PublicDataService
from .prices import PriceService
from .qr_provider import AddressQrProvider, QR_PROVIDER_ID
from .recovery_display import RecoverySecretDisplay
from .single_instance import ProcessInstance
from .transfer import TransferPreflightService
from .vault import VaultRepository

WINDOW_TITLE = "Holon Wallet"
MUTEX_NAME = r"Local\HolonWallet.M3.01"
_RECOVERY_TYPE_REGISTERED = False


class WalletQuickView(QQuickView):
    """Blocks ordinary close requests during the one-shot submission call."""

    def __init__(self, controller: WalletController) -> None:
        super().__init__()
        self._controller = controller

    def closeEvent(self, event: QCloseEvent) -> None:
        if not self._controller.canCloseWallet:
            event.ignore()
            return
        super().closeEvent(event)


class WalletApplication:
    """Owns the Qt runtime, controller, and QML-backed window."""

    def __init__(
        self,
        qt_app: QGuiApplication | None = None,
        repository: VaultRepository | None = None,
        public_data_service: PublicDataService | None = None,
        history_store: HistoryStore | None = None,
        public_data_executor: Executor | None = None,
        transfer_preflight_service: TransferPreflightService | None = None,
        transfer_executor: Executor | None = None,
        mainnet_executor: MainnetTransferExecutor | None = None,
        receipt_tracker: BroadcastReceiptTracker | None = None,
        receipt_executor: Executor | None = None,
        price_service: PriceService | None = None,
        allowance_service: AllowanceReadService | None = None,
        revoke_preflight_service: RevokePreflightService | None = None,
    ) -> None:
        self.qt_app = qt_app or QGuiApplication.instance()
        if self.qt_app is None:
            self.qt_app = QGuiApplication(sys.argv)
        self.qt_app.setApplicationDisplayName(WINDOW_TITLE)
        self.qt_app.setApplicationName("HolonWallet")
        font_package = files("holon_wallet.resources.fonts")
        with as_file(font_package.joinpath("InterVariable.ttf")) as font_path:
            font_id = QFontDatabase.addApplicationFont(str(font_path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if font_id < 0 or not families:
            raise RuntimeError("Bundled Wallet font could not be loaded")
        self.font_family = families[0]
        self.qt_app.setFont(QFont(self.font_family))
        qml_package = files("holon_wallet.qml")
        with as_file(qml_package.joinpath("assets/holon.svg")) as icon_path:
            self.qt_app.setWindowIcon(QIcon(str(icon_path)))

        self.controller = WalletController(
            repository,
            public_data_service,
            history_store,
            public_data_executor,
            transfer_preflight_service,
            transfer_executor,
            mainnet_executor,
            receipt_tracker,
            receipt_executor,
            price_service,
            allowance_service,
            revoke_preflight_service,
        )
        self.window = WalletQuickView(self.controller)
        global _RECOVERY_TYPE_REGISTERED
        if not _RECOVERY_TYPE_REGISTERED:
            qmlRegisterType(
                RecoverySecretDisplay,
                "Holon.Wallet",
                1,
                0,
                "RecoverySecretDisplay",
            )
            _RECOVERY_TYPE_REGISTERED = True
        self.engine = self.window.engine()
        self.engine.addImageProvider(QR_PROVIDER_ID, AddressQrProvider())
        self.qml_warnings: list[str] = []
        self.engine.warnings.connect(self._record_warnings)
        context = self.engine.rootContext()
        context.setContextProperty("walletController", self.controller)
        context.setContextProperty("walletWindow", self.window)
        context.setContextProperty("walletFontFamily", self.font_family)
        self.window.setTitle(WINDOW_TITLE)
        self.window.setColor(QColor("transparent"))
        self.window.setFlags(Qt.Window | Qt.FramelessWindowHint)
        self.window.setMinimumSize(QSize(430, 703))
        self.window.resize(430, 703)
        self.window.setResizeMode(QQuickView.SizeRootObjectToView)
        with as_file(qml_package.joinpath("Main.qml")) as qml_path:
            self.window.setSource(QUrl.fromLocalFile(str(qml_path)))
        if self.window.status() == QQuickView.Error or self.window.rootObject() is None:
            details = "; ".join(
                [*self.qml_warnings, *(error.toString() for error in self.window.errors())]
            ) or "unknown QML error"
            raise RuntimeError(f"Wallet QML failed to load: {details}")
        secret_display = self.window.rootObject().findChild(
            RecoverySecretDisplay,
            "recoverySecretDisplay",
        )
        if secret_display is None:
            raise RuntimeError("Recovery secret display could not be attached")
        secret_display.set_font_family(self.font_family)
        self.controller.attach_recovery_display(secret_display)
        self.window.visibleChanged.connect(self._handle_visibility)
        self.window.activeChanged.connect(
            lambda: self.controller.handleWindowActiveChanged(
                self.window.isActive(),
            ),
        )
        self.window.show()

    def _record_warnings(self, warnings: list[object]) -> None:
        self.qml_warnings.extend(str(warning.toString()) for warning in warnings)

    def run(self) -> int:
        return self.qt_app.exec()

    def close(self) -> None:
        self.controller.shutdown()
        self.window.close()
        self.window.deleteLater()
        self.qt_app.processEvents()

    def _handle_visibility(self) -> None:
        if not self.window.isVisible():
            self.controller.shutdown()


def main() -> int:
    instance = ProcessInstance(MUTEX_NAME, WINDOW_TITLE)
    if not instance.acquire():
        return 0
    try:
        return WalletApplication().run()
    finally:
        instance.release()
