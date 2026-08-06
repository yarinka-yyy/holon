"""Qt Quick application composition and standalone entry point."""

from __future__ import annotations

import sys
from concurrent.futures import Executor, ThreadPoolExecutor
from importlib.resources import as_file, files
from pathlib import Path
from threading import Event, Lock

from PySide6.QtCore import QObject, QSize, Qt, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QCloseEvent, QFont, QFontDatabase, QGuiApplication, QIcon
from PySide6.QtQml import qmlRegisterType
from PySide6.QtQuick import QQuickView
from holon_guard_ipc.wallet_status import WalletStatusClient
from holon_guard_ipc.policy_control import PolicyControlClient
from holon_policy import PolicyLoadError, PolicyRevisionStore, PolicyRevisionUnavailable
from holon_policy.baseline import (
    INSTALLED_POLICY_RELATIVE_PATH,
    load_baseline_policy,
)
from holon_modules import (
    CapabilityRegistry,
    default_catalog_path,
    load_registry as load_module_registry,
)
from holon_wallet_control import (
    AUTHORITY_PIPE_NAME, CONTROL_PIPE_NAME, WalletAuthorityServer,
    WalletControlServer,
)

from .approval import AllowanceReadService, RevokePreflightService
from .broadcast import (
    BroadcastReceiptTracker,
    MainnetBroadcastPolicy,
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
WALLET_INITIALIZATION_FAILED_EXIT_CODE = 20
WALLET_INSTANCE_UNREACHABLE_EXIT_CODE = 21
_RECOVERY_TYPE_REGISTERED = False


class _SerialStatusSender:
    """Keep Guard callbacks ordered without blocking the Qt event loop."""

    def __init__(self, client: WalletStatusClient) -> None:
        self._client = client
        self._executor = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="holon-wallet-status",
        )

    def send(self, update: dict[str, object]) -> None:
        self._executor.submit(self._client.send, dict(update))

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)


class _AuthorityRequestGate:
    """Choose exactly one timeout or Wallet delivery outcome."""

    def __init__(self) -> None:
        self.event = Event()
        self._lock = Lock()
        self._state = "PENDING"
        self._response: dict[str, object] | None = None

    def begin_delivery(self) -> bool:
        with self._lock:
            if self._state != "PENDING":
                return False
            self._state = "DELIVERING"
            return True

    def complete(self, response: dict[str, object]) -> bool:
        with self._lock:
            if self._state not in {"PENDING", "DELIVERING"}:
                return False
            self._response = dict(response)
            self._state = "COMPLETED"
            self.event.set()
            return True

    def timeout(self) -> bool:
        with self._lock:
            if self._state != "PENDING":
                return False
            self._state = "TIMED_OUT"
            return True

    @property
    def timed_out(self) -> bool:
        with self._lock:
            return self._state == "TIMED_OUT"

    @property
    def response(self) -> dict[str, object] | None:
        with self._lock:
            return None if self._response is None else dict(self._response)


def _wallet_policy_path() -> Path | None:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent / INSTALLED_POLICY_RELATIVE_PATH
    return None


def _load_wallet_transfer_policy(data_dir: Path | None = None) -> MainnetBroadcastPolicy:
    try:
        baseline = load_baseline_policy(_wallet_policy_path())
        if data_dir is None:
            return MainnetBroadcastPolicy.from_policy(baseline)
        store = PolicyRevisionStore(data_dir, baseline)
        snapshot, _changed = store.migrate_to_v4()
        return MainnetBroadcastPolicy.from_snapshot(snapshot, store)
    except (PolicyLoadError, PolicyRevisionUnavailable) as exc:
        code = getattr(exc, "code", "POLICY_STATE_INVALID")
        return MainnetBroadcastPolicy.unavailable(code)


class WalletQuickView(QQuickView):
    """Blocks ordinary close requests during the one-shot submission call."""

    def __init__(self, controller: WalletController) -> None:
        super().__init__()
        self._controller = controller

    def closeEvent(self, event: QCloseEvent) -> None:
        if self._controller.hideForLendingReceipt:
            self.hide()
            event.ignore()
            return
        if not self._controller.canCloseWallet:
            event.ignore()
            return
        super().closeEvent(event)


class _ControlBridge(QObject):
    activationRequested = Signal()

    def __init__(self, application: "WalletApplication") -> None:
        super().__init__()
        self._application = application
        self.activationRequested.connect(self._activate)

    def request_activation(self) -> None:
        self.activationRequested.emit()

    @Slot()
    def _activate(self) -> None:
        application = self._application
        application.window.showNormal()
        application.window.raise_()
        application.window.requestActivate()
        application.controller.showGuardOpenNotice()


class _AuthorityBridge(QObject):
    requested = Signal(object)
    timedOut = Signal(object)

    def __init__(
        self, application: "WalletApplication", response_timeout: float = 24.0,
    ) -> None:
        super().__init__()
        self._application = application
        self._response_timeout = response_timeout
        self.requested.connect(self._handle)
        self.timedOut.connect(self._handle_timeout)

    def request(self, request: dict[str, object]) -> dict[str, object]:
        gate = _AuthorityRequestGate()
        pending = {"request": request, "gate": gate}
        self.requested.emit(pending)
        if not gate.event.wait(self._response_timeout):
            if gate.timeout():
                self.timedOut.emit(pending)
                return WalletController._external_refusal(request, "WALLET_TIMEOUT")
            gate.event.wait()
        response = gate.response
        if response is None:
            return WalletController._external_refusal(request, "WALLET_TIMEOUT")
        return response

    @Slot(object)
    def _handle(self, pending: dict[str, object]) -> None:
        request = pending["request"]
        gate = pending["gate"]
        if not isinstance(request, dict) or not isinstance(gate, _AuthorityRequestGate):
            return
        if gate.timed_out:
            return
        application = self._application
        application.window.showNormal()
        application.window.raise_()
        application.window.requestActivate()

        def complete(response: dict[str, object]) -> None:
            gate.complete(response)

        if request.get("kind") in {"cancel_transfer", "cancel_action"}:
            complete(application.controller.cancelExternalTransfer(request))
        elif request.get("kind") == "prepare_lending_action":
            application.controller.prepareExternalLending(
                request, complete, gate.begin_delivery,
            )
        else:
            application.controller.prepareExternalTransfer(
                request, complete, gate.begin_delivery,
            )

    @Slot(object)
    def _handle_timeout(self, pending: dict[str, object]) -> None:
        request = pending.get("request")
        gate = pending.get("gate")
        if (
            isinstance(request, dict)
            and isinstance(gate, _AuthorityRequestGate)
            and gate.timed_out
            and request.get("kind") in {"prepare_transfer", "prepare_lending_action"}
        ):
            self._application.controller.expireExternalRequest(request)


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
        control_pipe_name: str | None = None,
        control_server_factory=WalletControlServer,
        authority_pipe_name: str | None = None,
        authority_server_factory=WalletAuthorityServer,
        status_client: WalletStatusClient | None = None,
        policy_control_client=None,
        lending_portfolio_service: object | None = None,
        module_registry: CapabilityRegistry | None = None,
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

        repository = repository or VaultRepository()
        if policy_control_client is None and getattr(sys, "frozen", False):
            policy_control_client = PolicyControlClient(
                Path(sys.executable).resolve().parent / "HolonGuard.exe",
            )
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
            transfer_policy=(
                _load_wallet_transfer_policy(repository.paths.data_dir)
                if mainnet_executor is None else None
            ),
            policy_control_client=policy_control_client,
            lending_portfolio_service=lending_portfolio_service,
            module_registry=(
                module_registry
                if module_registry is not None
                else load_module_registry(default_catalog_path(), "wallet")
            ),
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
        self._control_bridge: _ControlBridge | None = None
        self._control_server: WalletControlServer | None = None
        self._authority_bridge: _AuthorityBridge | None = None
        self._authority_server: WalletAuthorityServer | None = None
        self._status_sender: _SerialStatusSender | None = None
        if control_pipe_name is not None:
            self._control_bridge = _ControlBridge(self)
            self._control_server = control_server_factory(
                self._control_bridge.request_activation,
                pipe_name=control_pipe_name,
            )
            self._control_server.start()
        if authority_pipe_name is not None:
            self._authority_bridge = _AuthorityBridge(self)
            self._authority_server = authority_server_factory(
                self._authority_bridge.request,
                pipe_name=authority_pipe_name,
            )
            self._authority_server.start()
            status = status_client or WalletStatusClient()
            self._status_sender = _SerialStatusSender(status)
            self.controller.attach_guard_status_sender(self._status_sender.send)

    def _record_warnings(self, warnings: list[object]) -> None:
        self.qml_warnings.extend(str(warning.toString()) for warning in warnings)

    def run(self) -> int:
        return self.qt_app.exec()

    def close(self) -> None:
        if self._authority_server is not None:
            self._authority_server.stop()
            self._authority_server = None
        if self._control_server is not None:
            self._control_server.stop()
            self._control_server = None
        if self._status_sender is not None:
            self._status_sender.close()
            self._status_sender = None
        self.controller.shutdown()
        self.window.close()
        self.window.deleteLater()
        self.qt_app.processEvents()

    def _handle_visibility(self) -> None:
        if not self.window.isVisible():
            self.controller.shutdown()


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == ["--public-balances-worker"]:
        from .public_worker import run_public_balances_worker

        return run_public_balances_worker()
    if arguments == ["--lending-preview-worker"]:
        from .lending_worker import run_lending_preview_worker

        return run_lending_preview_worker()
    if arguments:
        return 2
    instance = ProcessInstance(MUTEX_NAME, WINDOW_TITLE)
    if not instance.acquire():
        return WALLET_INSTANCE_UNREACHABLE_EXIT_CODE
    application: WalletApplication | None = None
    try:
        try:
            application = WalletApplication(
                control_pipe_name=CONTROL_PIPE_NAME,
                authority_pipe_name=AUTHORITY_PIPE_NAME,
            )
        except Exception:
            return WALLET_INITIALIZATION_FAILED_EXIT_CODE
        return application.run()
    finally:
        if application is not None:
            application.close()
        instance.release()
