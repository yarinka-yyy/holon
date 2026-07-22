"""Secret-conscious QObject bridge for Wallet vault and QML flows."""

from __future__ import annotations

import hashlib
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event

from PySide6.QtCore import Property, QLocale, QObject, QTime, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from .broadcast import (
    BroadcastReceiptTracker,
    MainnetTransferCode,
    MainnetTransferExecutor,
    MainnetTransferResult,
    ReceiptTrackingResult,
    mainnet_result_to_map,
    result_from_tracking,
)
from .history import (
    HistoryStatus,
    HistoryStore,
    HistoryUnavailableError,
    HistoryValidationError,
    WalletHistoryRecord,
    history_record_to_map,
)
from .model import ProfileSummary, WalletShellState
from .public_data import (
    NETWORKS,
    NETWORK_BY_ID,
    NetworkSnapshot,
    PortfolioSnapshot,
    PublicDataService,
    PublicDataStatus,
    snapshot_to_map,
)
from .prices import (
    PriceService,
    PriceSnapshot,
    estimate_asset_usd,
    estimate_wei_usd,
    portfolio_to_map,
    price_snapshot_to_map,
)
from .recovery import (
    PreparedRecoveryAction,
    RecoveryActionError,
    RecoveryFlowCoordinator,
    RecoveryMaterialKind,
    recovery_action_to_map,
)
from .recovery_display import RecoverySecretDisplay
from .settings import SettingsStore
from .storage import StorageError
from .transfer import (
    PreparedTransferAction,
    TransferFlowCoordinator,
    TransferFlowState,
    TransferPreflightCode,
    TransferPreflightError,
    TransferPreflightService,
    format_atomic_amount,
    normalize_recipient,
    parse_transfer_amount,
    transfer_action_to_map,
    transfer_route,
)
from .vault import (
    MIN_PASSWORD_LENGTH,
    AuthenticationFailedError,
    PreparedVault,
    ProfileRecord,
    VaultRepository,
    VaultUnavailableError,
    VaultValidationError,
)
from .wallet_crypto import (
    MNEMONIC_PROFILE,
    RAW_KEY_PROFILE,
    InvalidSecretError,
    generate_mnemonic,
    import_mnemonic,
    import_private_key,
    private_key_bytes,
)


def _initials(profile: ProfileSummary) -> str:
    if profile.label == "Main Account":
        return "A1"
    if profile.label.startswith("Account "):
        return "A" + profile.label.removeprefix("Account ")[:1]
    return "".join(word[0] for word in profile.label.split())[:2].upper() or "A"


def _profile_map(profile: ProfileSummary) -> dict[str, object]:
    return {
        "id": profile.profile_id,
        "label": profile.label,
        "address": profile.address,
        "shortAddress": profile.short_address,
        "profileType": profile.profile_type,
        "typeLabel": (
            "Seed phrase" if profile.profile_type == MNEMONIC_PROFILE else "Private key"
        ),
        "derivationPath": profile.derivation_path or "",
        "createdAt": profile.created_at,
        "initials": _initials(profile),
    }


class WalletController(QObject):
    """Owns navigation and one bounded secret-bearing operation at a time."""

    profilesChanged = Signal()
    activeProfileChanged = Signal()
    currentScreenChanged = Signal()
    flowChanged = Signal()
    errorMessageChanged = Signal()
    backupWordsChanged = Signal()
    transferChanged = Signal()
    transferMaximumReady = Signal(str, str, str, str)
    publicDataChanged = Signal()
    selectedNetworkChanged = Signal()
    historyChanged = Signal()
    balancesVisibilityChanged = Signal()
    receiveNetworkChanged = Signal()
    historySelectionChanged = Signal()
    settingsSectionChanged = Signal()
    recoveryChanged = Signal()
    _publicDataReady = Signal(int, object)
    _transferReady = Signal(int, object)
    _maximumReady = Signal(int, object, str, str, str)
    _mainnetReady = Signal(int, object)
    _receiptReady = Signal(int, object)

    def __init__(
        self,
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
    ) -> None:
        super().__init__()
        self._repository = repository or VaultRepository()
        self._settings = SettingsStore(self._repository.paths)
        self._public_data_service = public_data_service or PublicDataService()
        self._price_service = price_service or PriceService()
        self._history_store = history_store or HistoryStore(self._repository.paths)
        self._public_data_executor = public_data_executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="holon-public-read",
        )
        self._owns_public_data_executor = public_data_executor is None
        self._transfer_preflight_service = (
            transfer_preflight_service or TransferPreflightService()
        )
        self._transfer_executor = transfer_executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="holon-critical-transfer",
        )
        self._owns_transfer_executor = transfer_executor is None
        self._mainnet_executor = mainnet_executor or MainnetTransferExecutor(
            self._repository,
            self._history_store,
        )
        self._receipt_tracker = receipt_tracker or BroadcastReceiptTracker(
            self._history_store,
        )
        self._receipt_executor = receipt_executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="holon-receipt-read",
        )
        self._owns_receipt_executor = receipt_executor is None
        self._state = WalletShellState()
        self._current_screen = "welcome"
        self._flow = "none"
        self._error_message = ""
        self._pending_record: ProfileRecord | None = None
        self._pending_vault: PreparedVault | None = None
        self._backup_words: tuple[str, ...] = ()
        self._transfer_flow = TransferFlowCoordinator()
        self._transfer_generation = 0
        self._maximum_generation = 0
        self._maximum_quoting = False
        self._transfer_preparing = False
        self._transfer_error = ""
        self._transfer_network = ""
        self._transfer_asset = ""
        self._transfer_recipient = ""
        self._transfer_amount_input = ""
        self._mainnet_in_progress = False
        self._mainnet_result: MainnetTransferResult | None = None
        self._receipt_checking = False
        self._receipt_generation = 0
        self._receipt_cancelled = Event()
        self._selected_network = "all"
        self._network_snapshots = {
            spec.network_id: NetworkSnapshot.unavailable(spec, "NOT_REFRESHED")
            for spec in NETWORKS
        }
        self._public_data_refreshing = False
        self._public_data_generation = 0
        self._public_data_updated_text = "Not refreshed"
        self._price_snapshot = PriceSnapshot.unavailable(
            int(datetime.now(UTC).timestamp()), "NOT_REFRESHED",
        )
        self._flow_price_snapshot: PriceSnapshot | None = None
        self._balances_visible = True
        self._receive_network = "base"
        self._history_records: tuple[WalletHistoryRecord, ...] = ()
        self._history_available = True
        self._selected_history_action_id = ""
        self._settings_section = ""
        self._wallets_return_screen = "settings"
        self._recovery_flow = RecoveryFlowCoordinator()
        self._recovery_selection = ""
        self._recovery_display: RecoverySecretDisplay | None = None
        self._recovery_copy_used = False
        self._recovery_clipboard_digest: bytes | None = None
        self._recovery_clipboard_seconds = 0
        self._recovery_reveal_seconds = 0
        self._recovery_reveal_kind = ""
        self._recovery_reveal_derivation_path = ""
        self._closed = False
        self._copied_phrase: str | None = None
        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setSingleShot(True)
        self._clipboard_timer.setInterval(60_000)
        self._clipboard_timer.timeout.connect(self._clear_clipboard)
        self._recovery_clipboard_timer = QTimer(self)
        self._recovery_clipboard_timer.setInterval(1_000)
        self._recovery_clipboard_timer.timeout.connect(
            self._tick_recovery_clipboard,
        )
        self._recovery_reveal_timer = QTimer(self)
        self._recovery_reveal_timer.setInterval(1_000)
        self._recovery_reveal_timer.timeout.connect(self._tick_recovery_reveal)
        self._transfer_expiry_timer = QTimer(self)
        self._transfer_expiry_timer.setSingleShot(True)
        self._transfer_expiry_timer.timeout.connect(self._expire_transfer)
        self._publicDataReady.connect(self._accept_public_data)
        self._transferReady.connect(self._accept_transfer_preflight)
        self._maximumReady.connect(self._accept_maximum_transfer)
        self._mainnetReady.connect(self._accept_mainnet_result)
        self._receiptReady.connect(self._accept_receipt_result)
        self._initialize()

    @Property("QVariantList", notify=profilesChanged)
    def profiles(self) -> list[dict[str, object]]:
        return [_profile_map(profile) for profile in self._state.profiles]

    @Property("QVariantList", notify=activeProfileChanged)
    def inactiveProfiles(self) -> list[dict[str, object]]:
        return [
            _profile_map(profile) for profile in self._state.profiles
            if profile.profile_id != self._state.active_profile_id
        ]

    @Property("QVariantMap", notify=activeProfileChanged)
    def activeProfile(self) -> dict[str, object]:
        active = self._state.active_profile
        return _profile_map(active) if active is not None else {}

    @Property(str, notify=activeProfileChanged)
    def activeProfileId(self) -> str:
        return self._state.active_profile_id or ""

    @Property(str, notify=currentScreenChanged)
    def currentScreen(self) -> str:
        return self._current_screen

    @Property(str, notify=errorMessageChanged)
    def errorMessage(self) -> str:
        return self._error_message

    @Property("QVariantList", notify=backupWordsChanged)
    def backupWords(self) -> list[str]:
        return list(self._backup_words)

    @Property(str, notify=flowChanged)
    def passwordTitle(self) -> str:
        return {
            "unlock": "Unlock Wallet",
            "create": "Set Password",
            "first_import": "Set Password",
            "add_private": "Confirm Password",
        }.get(self._flow, "Enter Password")

    @Property(str, notify=flowChanged)
    def passwordSubtitle(self) -> str:
        return {
            "unlock": "Enter your password to continue",
            "create": "4 characters min · longer is safer",
            "first_import": "4 characters min · longer is safer",
            "add_private": "Fresh authentication is required",
        }.get(self._flow, "")

    @Property(str, notify=flowChanged)
    def passwordActionLabel(self) -> str:
        return "Unlock" if self._flow == "unlock" else "Confirm"

    @Property(bool, notify=flowChanged)
    def passwordConfirmRequired(self) -> bool:
        return self._flow in {"create", "first_import"}

    @Property(bool, notify=flowChanged)
    def importPrivateOnly(self) -> bool:
        return self._flow == "add_private"

    @Property("QVariantMap", notify=transferChanged)
    def transferAction(self) -> dict[str, object]:
        action = self._transfer_flow.current
        return transfer_action_to_map(action) if action is not None else {}

    @Property(bool, notify=transferChanged)
    def transferPreparing(self) -> bool:
        return self._transfer_preparing

    @Property(bool, notify=transferChanged)
    def transferMaximumQuoting(self) -> bool:
        return self._maximum_quoting

    @Property(str, notify=transferChanged)
    def transferError(self) -> str:
        return self._transfer_error

    @Property(str, notify=transferChanged)
    def transferRecipient(self) -> str:
        return self._transfer_recipient

    @Property(str, notify=transferChanged)
    def transferNetwork(self) -> str:
        return self._transfer_network

    @Property(str, notify=transferChanged)
    def transferAsset(self) -> str:
        return self._transfer_asset

    @Property(str, notify=transferChanged)
    def transferAmountInput(self) -> str:
        return self._transfer_amount_input

    @Property(str, notify=transferChanged)
    def transferAvailableBalance(self) -> str:
        if self._transfer_network not in NETWORK_BY_ID or self._transfer_asset not in {
            "eth", "usdc",
        }:
            return "Select network and asset"
        snapshot = snapshot_to_map(self._network_snapshots[self._transfer_network])
        key = "ethValue" if self._transfer_asset == "eth" else "usdcValue"
        return str(snapshot.get(key) or "Data unavailable")

    @Slot(str, str, result=str)
    def maximumTransferAmount(self, network_id: str, asset_id: str) -> str:
        try:
            route = transfer_route(network_id, asset_id)
        except TransferPreflightError:
            return ""
        if asset_id == "eth":
            return ""
        snapshot = self._network_snapshots.get(network_id)
        if snapshot is None or snapshot.status is PublicDataStatus.UNAVAILABLE:
            return ""
        balance = snapshot.eth if asset_id == "eth" else snapshot.usdc
        if balance is None or balance.decimals != route.decimals:
            return ""
        maximum = self._mainnet_executor.policy.maximum_draft_amount(
            network_id, asset_id, balance.atomic_units,
        )
        return (
            format_atomic_amount(maximum, route.decimals)
            if maximum is not None else ""
        )

    @Property(bool, notify=transferChanged)
    def mainnetExecutionAvailable(self) -> bool:
        action = self._transfer_flow.current
        return (
            action is not None
            and self._transfer_flow.state is TransferFlowState.PREPARED
            and not self._mainnet_in_progress
            and self._mainnet_executor.policy.evaluate(action) is None
        )

    @Property(str, notify=transferChanged)
    def mainnetFeeLimit(self) -> str:
        action = self._transfer_flow.current
        return (
            self._mainnet_executor.policy.display_for(action)
            if action is not None else "Not configured"
        )

    @Property(str, notify=transferChanged)
    def mainnetAmountLimit(self) -> str:
        action = self._transfer_flow.current
        if action is None:
            return "Not configured"
        raw = self._mainnet_executor.policy.amount_display_for(action)
        if raw == "Not configured":
            return raw
        return f"≤ {format_atomic_amount(int(raw), action.decimals)} {action.token}"

    @Property(str, notify=transferChanged)
    def mainnetGateMessage(self) -> str:
        action = self._transfer_flow.current
        if action is None:
            return ""
        code = self._mainnet_executor.policy.evaluate(action)
        if code is MainnetTransferCode.POLICY_UNAVAILABLE:
            route = transfer_route(action.network_id, action.asset_id)
            prefix = action.network_id.upper()
            return (
                f"Configure HOLON_{prefix}_BROADCAST_ENABLED, "
                f"HOLON_{prefix}_MAX_TOTAL_FEE_WEI and {route.amount_cap_env}"
            )
        if code is MainnetTransferCode.FEE_LIMIT_EXCEEDED:
            return "Maximum fee exceeds the local mainnet limit"
        if code is MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED:
            return "Transfer amount exceeds the local route limit"
        return "Fresh password and explicit confirmation authorize one submission"

    @Property(bool, notify=transferChanged)
    def mainnetExecutionInProgress(self) -> bool:
        return self._mainnet_in_progress

    @Property("QVariantMap", notify=transferChanged)
    def mainnetResult(self) -> dict[str, object]:
        if self._mainnet_result is None:
            return {}
        return mainnet_result_to_map(self._mainnet_result)

    @Property(bool, notify=transferChanged)
    def receiptChecking(self) -> bool:
        return self._receipt_checking

    @Property(bool, notify=transferChanged)
    def canCloseWallet(self) -> bool:
        return not self._mainnet_in_progress

    @Property(str, notify=selectedNetworkChanged)
    def selectedNetwork(self) -> str:
        return self._selected_network

    @Property(bool, notify=publicDataChanged)
    def publicDataRefreshing(self) -> bool:
        return self._public_data_refreshing

    @Property(str, notify=publicDataChanged)
    def publicDataBanner(self) -> str:
        if self._public_data_refreshing:
            return "LOCAL WALLET  ·  REFRESHING PUBLIC DATA"
        statuses = [
            self._network_snapshots[network_id].status
            for network_id in self._selected_network_ids()
        ]
        if statuses and all(status is PublicDataStatus.LIVE for status in statuses):
            return "LOCAL WALLET  ·  LIVE PUBLIC DATA"
        if statuses and all(status is PublicDataStatus.SIMULATED for status in statuses):
            return "LOCAL WALLET  ·  SIMULATED PUBLIC DATA"
        if any(status in {PublicDataStatus.LIVE, PublicDataStatus.SIMULATED} for status in statuses):
            return "LOCAL WALLET  ·  PARTIAL PUBLIC DATA"
        return "LOCAL WALLET  ·  NETWORK DATA UNAVAILABLE"

    @Property(str, notify=publicDataChanged)
    def publicDataUpdatedText(self) -> str:
        return self._public_data_updated_text

    @Property("QVariantMap", notify=publicDataChanged)
    def ethereumData(self) -> dict[str, object]:
        return snapshot_to_map(self._network_snapshots["ethereum"])

    @Property("QVariantMap", notify=publicDataChanged)
    def baseData(self) -> dict[str, object]:
        return snapshot_to_map(self._network_snapshots["base"])

    @Property("QVariantMap", notify=publicDataChanged)
    def priceData(self) -> dict[str, object]:
        return price_snapshot_to_map(self._price_snapshot)

    @Property("QVariantMap", notify=publicDataChanged)
    def portfolioData(self) -> dict[str, object]:
        return portfolio_to_map(
            self._network_snapshots,
            self._price_snapshot,
            self._selected_network,
        )

    @Property(str, notify=transferChanged)
    def transferFeeUsd(self) -> str:
        action = self._transfer_flow.current
        prices = self._flow_price_snapshot or self._price_snapshot
        if action is None:
            return "Data unavailable"
        return estimate_wei_usd(action.max_total_fee_wei, prices)

    @Property(str, notify=transferChanged)
    def transferAmountUsd(self) -> str:
        action = self._transfer_flow.current
        prices = self._flow_price_snapshot or self._price_snapshot
        if action is None:
            return "Data unavailable"
        return estimate_asset_usd(
            action.amount_atomic,
            action.decimals,
            action.asset_id,
            prices,
        )

    @Property(bool, notify=balancesVisibilityChanged)
    def balancesVisible(self) -> bool:
        return self._balances_visible

    @Property(str, notify=receiveNetworkChanged)
    def receiveNetwork(self) -> str:
        return self._receive_network

    @Property(str, notify=activeProfileChanged)
    def receiveQrSource(self) -> str:
        active = self._state.active_profile
        return f"image://walletQr/{active.address}" if active is not None else ""

    @Property("QVariantList", notify=historyChanged)
    def historyRecords(self) -> list[dict[str, object]]:
        mapped: list[dict[str, object]] = []
        previous_date = ""
        for record in sorted(
            (
                item for item in self._history_records
                if item.profile_id == self._state.active_profile_id
            ),
            key=lambda item: item.created_at,
            reverse=True,
        ):
            value = history_record_to_map(record)
            current_date = str(value["dateLabel"])
            value["showDateHeader"] = current_date != previous_date
            previous_date = current_date
            mapped.append(value)
        return mapped

    @Property(bool, notify=historyChanged)
    def historyAvailable(self) -> bool:
        return self._history_available

    @Property(str, notify=historyChanged)
    def historyStateLabel(self) -> str:
        if not self._history_available:
            return "History unavailable"
        if not self.historyRecords:
            return "No Wallet-initiated transactions yet"
        return ""

    @Property("QVariantMap", notify=historyChanged)
    def selectedHistoryRecord(self) -> dict[str, object]:
        record = next(
            (
                item for item in self._history_records
                if item.action_id == self._selected_history_action_id
                and item.profile_id == self._state.active_profile_id
            ),
            None,
        )
        return history_record_to_map(record) if record is not None else {}

    @Property(str, notify=settingsSectionChanged)
    def settingsSection(self) -> str:
        return self._settings_section

    @Property(str, notify=recoveryChanged)
    def recoverySelection(self) -> str:
        return self._recovery_selection

    @Property(bool, notify=activeProfileChanged)
    def recoverySeedAvailable(self) -> bool:
        active = self._state.active_profile
        return active is not None and active.profile_type == MNEMONIC_PROFILE

    @Property("QVariantMap", notify=recoveryChanged)
    def recoveryAction(self) -> dict[str, object]:
        action = self._recovery_flow.current
        return recovery_action_to_map(action) if action is not None else {}

    @Property(bool, notify=recoveryChanged)
    def recoveryCopyUsed(self) -> bool:
        return self._recovery_copy_used

    @Property(int, notify=recoveryChanged)
    def recoveryClipboardSeconds(self) -> int:
        return self._recovery_clipboard_seconds

    @Property(int, notify=recoveryChanged)
    def recoveryRevealSeconds(self) -> int:
        return self._recovery_reveal_seconds

    @Property(str, notify=recoveryChanged)
    def recoveryRevealKind(self) -> str:
        return self._recovery_reveal_kind

    @Property(str, notify=recoveryChanged)
    def recoveryRevealDerivationPath(self) -> str:
        return self._recovery_reveal_derivation_path

    @Property("QVariantList", notify=currentScreenChanged)
    def transactionFlowSteps(self) -> list[str]:
        return ["Review", "Confirm", "Submit", "Complete"]

    @Property(int, notify=currentScreenChanged)
    def transactionFlowStage(self) -> int:
        return {
            "transfer_review": 0,
            "sign_transfer": 1,
            "submit_transfer": 2,
            "transfer_result": 3,
        }.get(self._current_screen, 0)

    @Slot()
    def beginCreate(self) -> None:
        if self._repository.exists:
            return
        self._begin_flow("create", "password")

    @Slot()
    def beginImport(self) -> None:
        if self._repository.exists:
            return
        self._begin_flow("first_import", "import")

    @Slot()
    def beginAddPrivateKey(self) -> None:
        if not self._state.profiles:
            return
        self._begin_flow("add_private", "import")

    @Slot()
    def showSend(self) -> None:
        if (
            not self._state.profiles
            or self._mainnet_in_progress
            or self._recovery_flow.current is not None
            or self._current_screen == "recovery_reveal"
        ):
            return
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._set_screen("send")

    @Slot(str, result=bool)
    @Slot(str, str, str, str, result=bool)
    def prepareTransfer(
        self,
        network_id: str,
        asset_id: str | None = None,
        recipient: str | None = None,
        amount_input: str | None = None,
    ) -> bool:
        if asset_id is None and recipient is None and amount_input is None:
            recipient = network_id
            network_id = "base"
            asset_id = "usdc"
            amount_input = "1"
        if recipient is None or amount_input is None:
            return False
        active = self._state.active_profile
        if active is None or self._closed or self._transfer_preparing:
            return False
        self._set_transfer_error("")
        try:
            route = transfer_route(network_id, asset_id)
            amount_atomic, canonical_amount = parse_transfer_amount(
                amount_input, route.decimals,
            )
            normalized = normalize_recipient(recipient, active.address)
            amount_code = self._mainnet_executor.policy.draft_amount_code(
                network_id, asset_id, amount_atomic,
            )
            if amount_code is MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED:
                raise TransferPreflightError(
                    TransferPreflightCode.AMOUNT_LIMIT_EXCEEDED,
                )
            request = self._transfer_flow.begin(
                active.profile_id, network_id, asset_id, amount_atomic,
            )
        except TransferPreflightError as error:
            self._set_transfer_error(_transfer_error_message(error.code))
            return False
        except RuntimeError:
            return False
        self._transfer_network = network_id
        self._transfer_asset = asset_id
        self._transfer_recipient = normalized
        self._transfer_amount_input = canonical_amount
        self._transfer_preparing = True
        self._maximum_generation += 1
        self._maximum_quoting = False
        self._transfer_generation += 1
        generation = self._transfer_generation
        self.transferChanged.emit()
        future = self._transfer_executor.submit(
            self._transfer_preflight_service.prepare,
            request,
            active,
            normalized,
        )
        future.add_done_callback(
            lambda completed, current=generation: self._transfer_finished(
                current, completed,
            ),
        )
        return True

    @Slot(str, str, str, result=bool)
    def requestMaximumTransfer(
        self, network_id: str, asset_id: str, recipient: str,
    ) -> bool:
        active = self._state.active_profile
        if (
            active is None
            or self._closed
            or self._transfer_preparing
            or self._maximum_quoting
        ):
            return False
        self._set_transfer_error("")
        try:
            route = transfer_route(network_id, asset_id)
            if asset_id == "usdc":
                amount = self.maximumTransferAmount(network_id, asset_id)
                if not amount:
                    raise TransferPreflightError(
                        TransferPreflightCode.INSUFFICIENT_USDC,
                    )
                self.transferMaximumReady.emit(
                    network_id, asset_id, recipient, amount,
                )
                return True
            normalized = normalize_recipient(recipient, active.address)
        except TransferPreflightError as error:
            self._set_transfer_error(_transfer_error_message(error.code))
            return False
        self._maximum_generation += 1
        generation = self._maximum_generation
        self._maximum_quoting = True
        self.transferChanged.emit()
        future = self._transfer_executor.submit(
            self._transfer_preflight_service.quote_maximum_native,
            active,
            route.network_id,
            normalized,
        )
        future.add_done_callback(
            lambda completed, current=generation: self._maximum_finished(
                current,
                completed,
                network_id,
                asset_id,
                recipient,
            ),
        )
        return True

    @Slot(result=str)
    def pasteTransferRecipient(self) -> str:
        return QGuiApplication.clipboard().text()

    @Slot(result=bool)
    def copyActiveAddress(self) -> bool:
        active = self._state.active_profile
        if active is None:
            return False
        QGuiApplication.clipboard().setText(active.address)
        return True

    @Slot()
    def toggleBalancesVisibility(self) -> None:
        self._balances_visible = not self._balances_visible
        self.balancesVisibilityChanged.emit()

    @Slot(str, result=bool)
    def selectReceiveNetwork(self, network_id: str) -> bool:
        if network_id not in {"ethereum", "base"}:
            return False
        if network_id != self._receive_network:
            self._receive_network = network_id
            self.receiveNetworkChanged.emit()
        return True

    @Slot()
    def cancelTransfer(self) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._set_screen("main")

    @Slot()
    def editTransfer(self) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        action = self._transfer_flow.current
        if action is None:
            self._cancel_transfer_request(clear_recipient=False)
            self._set_screen("send")
            return
        self._transfer_network = action.network_id
        self._transfer_asset = action.asset_id
        self._transfer_recipient = action.recipient
        self._transfer_amount_input = format_atomic_amount(
            action.amount_atomic, action.decimals,
        )
        self._cancel_transfer_request(clear_recipient=False)
        self._set_screen("send")

    @Slot()
    def finishTransfer(self) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._set_screen("main")

    @Slot(result=bool)
    def beginMainnetExecution(self) -> bool:
        action = self._transfer_flow.current
        if (
            action is None
            or self._closed
            or self._transfer_flow.state is not TransferFlowState.PREPARED
            or self._mainnet_executor.policy.evaluate(action) is not None
        ):
            return False
        if self._transfer_flow.is_expired():
            self._show_mainnet_failure(action, MainnetTransferCode.ACTION_EXPIRED)
            return False
        self._clear_mainnet_result()
        self._set_screen("sign_transfer")
        return True

    @Slot(str, bool, result=bool)
    def submitMainnetExecution(self, password: str, explicitly_confirmed: bool) -> bool:
        action = self._transfer_flow.current
        active = self._state.active_profile
        if (
            len(password) < MIN_PASSWORD_LENGTH
            or not explicitly_confirmed
            or action is None
            or active is None
            or self._closed
            or self._current_screen != "sign_transfer"
            or self._mainnet_in_progress
        ):
            return False
        expected_digest = self._transfer_flow.accepted_digest
        permit = self._transfer_flow.begin_execution(
            action.action_id,
            expected_digest,
            active.profile_id,
        )
        if permit is None:
            self._show_mainnet_failure(action, MainnetTransferCode.ACTION_INVALID)
            return False
        self._mainnet_in_progress = True
        self._mainnet_result = None
        self._transfer_expiry_timer.stop()
        self._transfer_generation += 1
        generation = self._transfer_generation
        self.transferChanged.emit()
        self._set_screen("submit_transfer")
        future = self._transfer_executor.submit(
            self._mainnet_executor.execute,
            action,
            expected_digest,
            password,
            permit,
        )
        del password
        future.add_done_callback(
            lambda completed, current=generation: self._mainnet_finished(
                current, completed,
            ),
        )
        return True

    @Slot()
    def cancelMainnetExecution(self) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._set_screen("main")

    @Slot()
    def finishMainnetExecution(self) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._set_screen("main")

    @Slot(str, result=bool)
    def checkMainnetStatus(self, action_id: str) -> bool:
        if self._closed or self._receipt_checking:
            return False
        record = next(
            (
                item for item in self._history_records
                if item.action_id == action_id
                and item.profile_id == self._state.active_profile_id
            ),
            None,
        )
        if (
            record is None
            or record.transaction_hash is None
            or record.status not in {HistoryStatus.PENDING, HistoryStatus.UNKNOWN}
        ):
            return False
        return self._start_receipt_check(action_id, track=False)

    @Slot(str, result=bool)
    def selectNetwork(self, network_id: str) -> bool:
        if network_id not in {"all", *NETWORK_BY_ID}:
            return False
        if network_id != self._selected_network:
            self._selected_network = network_id
            self.selectedNetworkChanged.emit()
        if self._current_screen == "main":
            self.refreshPublicData()
        return True

    @Slot(result=bool)
    def refreshPublicData(self) -> bool:
        active = self._state.active_profile
        if active is None or self._closed:
            return False
        network_ids = self._selected_network_ids()
        self._public_data_generation += 1
        generation = self._public_data_generation
        self._public_data_refreshing = True
        self._public_data_updated_text = "Refreshing…"
        for network_id in network_ids:
            self._network_snapshots[network_id] = NetworkSnapshot.unavailable(
                NETWORK_BY_ID[network_id], "REFRESHING",
            )
        self.publicDataChanged.emit()
        future = self._public_data_executor.submit(
            self._refresh_public_bundle,
            active.profile_id,
            active.address,
            network_ids,
        )
        future.add_done_callback(
            lambda completed, current=generation: self._public_data_finished(
                current, completed,
            ),
        )
        return True

    @Slot(str, str, result=bool)
    def submitImport(self, import_type: str, value: str) -> bool:
        self._set_error("")
        try:
            if self._flow == "add_private":
                if import_type != "private":
                    return False
                secret = import_private_key(value)
            elif import_type == "seed":
                secret = import_mnemonic(value)
            elif import_type == "private":
                secret = import_private_key(value)
            else:
                return False
            self._pending_record = self._repository.new_record(
                secret, self._next_label(),
            )
        except InvalidSecretError as error:
            self._set_error(str(error))
            return False
        self._set_screen("password")
        return True

    @Slot(str, str, result=bool)
    def submitPassword(self, password: str, confirmation: str) -> bool:
        self._set_error("")
        if self.passwordConfirmRequired and password != confirmation:
            self._set_error("Passwords do not match")
            return False
        if len(password) < MIN_PASSWORD_LENGTH:
            self._set_error("Password must contain at least 4 characters")
            return False
        try:
            if self._flow == "unlock":
                profiles = self._repository.authenticate(password)
                self._replace_profiles(profiles)
                self._flow = "none"
                self.flowChanged.emit()
                self._set_screen("main")
            elif self._flow == "create":
                secret = generate_mnemonic()
                record = self._repository.new_record(secret, "Main Account")
                self._pending_vault = self._repository.prepare_new(password, record)
                self._pending_record = record
                self._backup_words = tuple(secret.value.split())
                self.backupWordsChanged.emit()
                self._set_screen("backup")
            elif self._flow == "first_import" and self._pending_record is not None:
                profiles = self._repository.create_new(password, self._pending_record)
                self._complete_profile_operation(profiles, profiles[0].profile_id, "main")
            elif self._flow == "add_private" and self._pending_record is not None:
                added_id = self._pending_record.summary.profile_id
                profiles = self._repository.append(password, self._pending_record)
                self._complete_profile_operation(profiles, added_id, "wallets")
            else:
                return False
            return True
        except AuthenticationFailedError:
            self._set_error("Authentication failed")
        except VaultUnavailableError:
            self._clear_sensitive()
            self._flow = "none"
            self.flowChanged.emit()
            self._set_screen("unavailable")
        except StorageError:
            self._set_error("Wallet could not be saved")
        except VaultValidationError as error:
            self._set_error(str(error))
        return False

    @Slot(result=bool)
    def finishBackup(self) -> bool:
        if self._pending_vault is None:
            return False
        self._set_error("")
        try:
            self._repository.commit_new(self._pending_vault)
            profiles = self._pending_vault.profiles
            self._complete_profile_operation(profiles, profiles[0].profile_id, "main")
            return True
        except (StorageError, VaultValidationError):
            self._set_error("Wallet could not be saved")
            return False

    @Slot(result=bool)
    def copyBackup(self) -> bool:
        if not self._backup_words:
            return False
        phrase = " ".join(self._backup_words)
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(phrase)
        self._copied_phrase = phrase
        self._clipboard_timer.start()
        return True

    @Slot()
    def cancelFlow(self) -> None:
        destination = "wallets" if self._flow == "add_private" else "welcome"
        if self._flow == "unlock":
            return
        self._clear_sensitive()
        self._flow = "none"
        self.flowChanged.emit()
        self._set_error("")
        self._set_screen(destination)

    @Slot()
    def retryUnavailable(self) -> None:
        self._initialize()

    @Slot(str, result=bool)
    def selectProfile(self, profile_id: str) -> bool:
        if self._mainnet_in_progress:
            return False
        if not any(profile.profile_id == profile_id for profile in self._state.profiles):
            return False
        try:
            self._settings.save_active_id(profile_id)
        except StorageError:
            self._set_error("Active Account could not be saved")
            return False
        if self._state.select_profile(profile_id):
            recovery_open = self._current_screen.startswith("recovery_")
            invalidated = self._transfer_flow.profile_changed(profile_id)
            recovery_invalidated = self._recovery_flow.profile_changed(profile_id)
            if recovery_invalidated or recovery_open:
                self._cancel_recovery_action(clear_clipboard=True)
            self.activeProfileChanged.emit()
            self.historyChanged.emit()
            if invalidated or recovery_invalidated or recovery_open:
                self._transfer_generation += 1
                self._transfer_preparing = False
                self._mainnet_in_progress = False
                self._mainnet_result = None
                self._transfer_recipient = ""
                self._set_transfer_error("")
                self.transferChanged.emit()
                self._set_screen("main")
            elif self._current_screen in {"main", "history"}:
                self.refreshPublicData()
            return True
        return False

    @Slot()
    def showMain(self) -> None:
        if self._state.profiles and not self._mainnet_in_progress:
            if self._current_screen.startswith("recovery_"):
                self._cancel_recovery_action(clear_clipboard=False)
            self._set_screen("main")

    @Slot()
    def showReceive(self) -> None:
        if self._state.profiles and not self._mainnet_in_progress:
            self._receive_network = (
                self._selected_network
                if self._selected_network in {"ethereum", "base"}
                else "base"
            )
            self.receiveNetworkChanged.emit()
            self._set_screen("receive")

    @Slot()
    def showSettings(self) -> None:
        if self._state.profiles and not self._mainnet_in_progress:
            self._settings_section = ""
            self.settingsSectionChanged.emit()
            self._set_screen("settings")

    @Slot(str, result=bool)
    def showSettingsSection(self, section: str) -> bool:
        if section not in {"network", "security", "about"}:
            return False
        self._settings_section = section
        self.settingsSectionChanged.emit()
        self._set_screen("settings_info")
        return True

    @Slot()
    def showWallets(self) -> None:
        if self._state.profiles and not self._mainnet_in_progress:
            self._wallets_return_screen = (
                "settings" if self._current_screen == "settings" else "main"
            )
            self._set_screen("wallets")

    @Slot()
    def showHistory(self) -> None:
        if self._state.profiles and not self._mainnet_in_progress:
            self._load_history()
            self._set_screen("history")

    @Slot(str, result=bool)
    def showTransactionDetails(self, action_id: str) -> bool:
        if not any(
            item.action_id == action_id
            and item.profile_id == self._state.active_profile_id
            for item in self._history_records
        ):
            return False
        self._selected_history_action_id = action_id
        self.historySelectionChanged.emit()
        self.historyChanged.emit()
        self._set_screen("transaction_details")
        return True

    @Slot()
    def closeTransactionDetails(self) -> None:
        self._selected_history_action_id = ""
        self.historySelectionChanged.emit()
        self.historyChanged.emit()
        self._set_screen("history")

    @Slot()
    def closeWallets(self) -> None:
        self._set_screen(self._wallets_return_screen)

    @Slot()
    def closeSettingsInfo(self) -> None:
        self._settings_section = ""
        self.settingsSectionChanged.emit()
        self._set_screen("settings")

    @Slot()
    def showRecoveryReview(self) -> None:
        active = self._state.active_profile
        if (
            active is None
            or self._closed
            or self._mainnet_in_progress
            or self._transfer_flow.current is not None
        ):
            return
        self._cancel_recovery_action(clear_clipboard=False)
        self._recovery_selection = (
            RecoveryMaterialKind.SEED_PHRASE.value
            if active.profile_type == MNEMONIC_PROFILE
            else RecoveryMaterialKind.PRIVATE_KEY.value
        )
        self._set_error("")
        self.recoveryChanged.emit()
        self._set_screen("recovery_review")

    @Slot(str, result=bool)
    def selectRecoveryMaterial(self, material_kind: str) -> bool:
        active = self._state.active_profile
        if active is None or self._current_screen != "recovery_review":
            return False
        try:
            selected = RecoveryMaterialKind(material_kind)
        except ValueError:
            return False
        if (
            selected is RecoveryMaterialKind.SEED_PHRASE
            and active.profile_type != MNEMONIC_PROFILE
        ):
            return False
        if material_kind != self._recovery_selection:
            self._recovery_flow.cancel()
            self._recovery_selection = material_kind
            self._set_error("")
            self.recoveryChanged.emit()
        return True

    @Slot(result=bool)
    def prepareRecovery(self) -> bool:
        active = self._state.active_profile
        if active is None or self._current_screen != "recovery_review":
            return False
        try:
            material_kind = RecoveryMaterialKind(self._recovery_selection)
            self._recovery_flow.cancel()
            self._recovery_flow.prepare(active, material_kind)
        except (ValueError, RecoveryActionError):
            self._set_error("Recovery material is unavailable for this Account")
            return False
        self._set_error("")
        self.recoveryChanged.emit()
        self._set_screen("recovery_confirm")
        return True

    @Slot()
    def editRecovery(self) -> None:
        if self._current_screen != "recovery_confirm":
            return
        self._recovery_flow.cancel()
        self._set_error("")
        self.recoveryChanged.emit()
        self._set_screen("recovery_review")

    @Slot(str, bool, result=bool)
    def submitRecovery(self, password: str, explicitly_confirmed: bool) -> bool:
        action = self._recovery_flow.current
        active = self._state.active_profile
        if (
            len(password) < MIN_PASSWORD_LENGTH
            or not explicitly_confirmed
            or action is None
            or active is None
            or self._recovery_display is None
            or self._closed
            or self._current_screen != "recovery_confirm"
        ):
            return False
        action_id = action.action_id
        digest = action.digest
        try:
            self._recovery_flow.preflight(action_id, digest, active)
            record = self._repository.authenticate_profile(password, active.profile_id)
        except AuthenticationFailedError:
            self._recovery_flow.authentication_failed()
            self._set_error("Authentication failed · start a new recovery action")
            self.recoveryChanged.emit()
            self._set_screen("recovery_review")
            return False
        except RecoveryActionError as error:
            self._set_error(str(error))
            self.recoveryChanged.emit()
            self._set_screen("recovery_review")
            return False
        except VaultUnavailableError:
            self._cancel_recovery_action(clear_clipboard=True)
            self._set_screen("unavailable")
            return False
        finally:
            del password
        try:
            value = self._recovery_flow.authorize_and_consume(
                action_id,
                digest,
                active,
                lambda current: _recovery_value(record, current),
            )
            self._recovery_display.set_material(action.material_kind, value)
        except (RecoveryActionError, InvalidSecretError, VaultValidationError):
            self._cancel_recovery_action(clear_clipboard=True)
            self._set_error("Recovery material could not be verified")
            self._set_screen("recovery_review")
            return False
        finally:
            if "value" in locals():
                del value
            del record
        self._recovery_copy_used = False
        self._recovery_reveal_seconds = 60
        self._recovery_reveal_kind = action.material_kind.value
        self._recovery_reveal_derivation_path = action.derivation_path or ""
        self._recovery_reveal_timer.start()
        self._set_error("")
        self.recoveryChanged.emit()
        self._set_screen("recovery_reveal")
        return True

    @Slot(result=bool)
    def copyRecoveryMaterial(self) -> bool:
        if (
            self._current_screen != "recovery_reveal"
            or self._recovery_copy_used
            or self._recovery_display is None
        ):
            return False
        value = self._recovery_display.copy_text()
        if value is None:
            return False
        clipboard = QGuiApplication.clipboard()
        clipboard.setText(value)
        self._recovery_clipboard_digest = hashlib.sha256(
            value.encode("utf-8"),
        ).digest()
        del value
        self._recovery_copy_used = True
        self._recovery_clipboard_seconds = 30
        self._recovery_clipboard_timer.start()
        self.recoveryChanged.emit()
        return True

    @Slot()
    def finishRecovery(self) -> None:
        if self._current_screen not in {
            "recovery_review", "recovery_confirm", "recovery_reveal",
        }:
            return
        self._cancel_recovery_action(clear_clipboard=False)
        self._settings_section = "security"
        self.settingsSectionChanged.emit()
        self._set_screen("settings_info")

    @Slot(bool)
    def handleWindowActiveChanged(self, active: bool) -> None:
        if active or self._current_screen != "recovery_reveal":
            return
        self._cancel_recovery_action(clear_clipboard=False)
        self._settings_section = "security"
        self.settingsSectionChanged.emit()
        self._set_error("Recovery material was hidden when Wallet lost focus")
        self._set_screen("settings_info")

    def attach_recovery_display(self, display: RecoverySecretDisplay) -> None:
        self._recovery_display = display

    @Slot()
    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._public_data_generation += 1
        self._receipt_generation += 1
        self._receipt_cancelled.set()
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._clear_sensitive()
        if self._owns_public_data_executor:
            self._public_data_executor.shutdown(wait=False, cancel_futures=True)
        if self._owns_transfer_executor:
            self._transfer_executor.shutdown(wait=False, cancel_futures=True)
        if self._owns_receipt_executor:
            self._receipt_executor.shutdown(wait=False, cancel_futures=True)

    def _initialize(self) -> None:
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._clear_sensitive()
        self._set_error("")
        if not self._repository.exists:
            self._state = WalletShellState()
            self.profilesChanged.emit()
            self.activeProfileChanged.emit()
            self._flow = "none"
            self.flowChanged.emit()
            self._set_screen("welcome")
            return
        try:
            profiles = self._repository.load_public()
            self._replace_profiles(profiles)
            self._flow = "unlock"
            self.flowChanged.emit()
            self._set_screen("password")
        except VaultUnavailableError:
            self._state = WalletShellState()
            self.profilesChanged.emit()
            self.activeProfileChanged.emit()
            self._flow = "none"
            self.flowChanged.emit()
            self._set_screen("unavailable")

    def _replace_profiles(
        self, profiles: tuple[ProfileSummary, ...], active_id: str | None = None,
    ) -> None:
        valid_ids = {profile.profile_id for profile in profiles}
        selected = active_id or self._settings.load_active_id(valid_ids)
        self._state.replace_profiles(profiles, selected)
        self.profilesChanged.emit()
        self.activeProfileChanged.emit()
        self._load_history()

    def _complete_profile_operation(
        self, profiles: tuple[ProfileSummary, ...], active_id: str, screen: str,
    ) -> None:
        try:
            self._settings.save_active_id(active_id)
        except StorageError:
            pass
        self._replace_profiles(profiles, active_id)
        self._clear_sensitive()
        self._flow = "none"
        self.flowChanged.emit()
        self._set_screen(screen)

    def _begin_flow(self, flow: str, screen: str) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._clear_sensitive()
        self._set_error("")
        self._flow = flow
        self.flowChanged.emit()
        self._set_screen(screen)

    def _next_label(self) -> str:
        return "Main Account" if not self._state.profiles else f"Account {len(self._state.profiles) + 1}"

    def _clear_sensitive(self) -> None:
        self._clear_clipboard()
        self._cancel_recovery_action(clear_clipboard=True)
        self._pending_record = None
        self._pending_vault = None
        if self._backup_words:
            self._backup_words = ()
            self.backupWordsChanged.emit()

    def _load_history(self) -> None:
        try:
            records = self._history_store.load()
            available = True
        except HistoryUnavailableError:
            records = ()
            available = False
        if records != self._history_records or available != self._history_available:
            self._history_records = records
            self._history_available = available
            self.historyChanged.emit()

    def _transfer_finished(
        self, generation: int, future: Future[PreparedTransferAction],
    ) -> None:
        if self._closed:
            return
        try:
            result: object = future.result()
        except TransferPreflightError as error:
            result = error
        except Exception:
            result = TransferPreflightError(TransferPreflightCode.RPC_UNAVAILABLE)
        self._transferReady.emit(generation, result)

    def _maximum_finished(
        self,
        generation: int,
        future: Future[int],
        network_id: str,
        asset_id: str,
        recipient: str,
    ) -> None:
        if self._closed:
            return
        try:
            result: object = future.result()
        except TransferPreflightError as error:
            result = error
        except Exception:
            result = TransferPreflightError(TransferPreflightCode.RPC_UNAVAILABLE)
        self._maximumReady.emit(
            generation, result, network_id, asset_id, recipient,
        )

    def _mainnet_finished(
        self, generation: int, future: Future[MainnetTransferResult],
    ) -> None:
        if self._closed:
            return
        try:
            result: object = future.result()
        except Exception:
            result = None
        self._mainnetReady.emit(generation, result)

    @Slot(int, object)
    def _accept_mainnet_result(self, generation: int, result: object) -> None:
        if generation != self._transfer_generation or self._closed:
            return
        action = self._transfer_flow.current
        self._mainnet_in_progress = False
        if (
            not isinstance(result, MainnetTransferResult)
            or action is None
            or result.action_id != action.action_id
            or not self._transfer_flow.complete_execution(action.action_id)
        ):
            if action is None:
                return
            result = self._safe_mainnet_result(
                action, MainnetTransferCode.SIGNING_FAILED,
            )
            self._transfer_flow.close()
        self._transfer_expiry_timer.stop()
        self._mainnet_result = result
        self._load_history()
        self.transferChanged.emit()
        self._set_screen("transfer_result")
        if (
            result.transaction_hash
            and result.history_status in {HistoryStatus.PENDING, HistoryStatus.UNKNOWN}
        ):
            self._start_receipt_check(result.action_id, track=True)

    def _receipt_finished(
        self, generation: int, future: Future[ReceiptTrackingResult],
    ) -> None:
        if self._closed:
            return
        try:
            result: object = future.result()
        except Exception:
            result = None
        self._receiptReady.emit(generation, result)

    @Slot(int, object)
    def _accept_receipt_result(self, generation: int, result: object) -> None:
        if generation != self._receipt_generation or self._closed:
            return
        self._receipt_checking = False
        if isinstance(result, ReceiptTrackingResult):
            if (
                self._mainnet_result is not None
                and result.action_id == self._mainnet_result.action_id
            ):
                self._mainnet_result = result_from_tracking(
                    self._mainnet_result,
                    result,
                )
            self._load_history()
        self.transferChanged.emit()

    @Slot(int, object)
    def _accept_transfer_preflight(self, generation: int, result: object) -> None:
        if generation != self._transfer_generation or self._closed:
            return
        self._transfer_preparing = False
        active = self._state.active_profile
        if isinstance(result, TransferPreflightError):
            self._transfer_flow.close()
            self._set_transfer_error(_transfer_error_message(result.code))
            self.transferChanged.emit()
            return
        if not isinstance(result, PreparedTransferAction) or active is None:
            self._transfer_flow.close()
            self._set_transfer_error("Transaction preparation failed")
            self.transferChanged.emit()
            return
        if (
            active.profile_id != result.profile_id
            or active.address != result.sender
            or not self._transfer_flow.still_pending(
                result.action_id, result.profile_id,
            )
            or not self._transfer_flow.accept(result)
        ):
            self._transfer_flow.close()
            self._set_transfer_error("Transaction preparation expired")
            self.transferChanged.emit()
            return
        try:
            records = self._history_store.append(_history_record(result))
        except (
            HistoryUnavailableError,
            HistoryValidationError,
            StorageError,
        ):
            self._transfer_flow.close()
            self._history_available = False
            self.historyChanged.emit()
            self._set_transfer_error("History unavailable · transaction was not prepared")
            self.transferChanged.emit()
            return
        self._history_records = records
        self._history_available = True
        self._flow_price_snapshot = self._price_snapshot
        self.historyChanged.emit()
        remaining_ms = max(
            1,
            int((result.expires_at - datetime.now(UTC)).total_seconds() * 1000) + 1,
        )
        self._transfer_expiry_timer.start(remaining_ms)
        self._set_transfer_error("")
        self.transferChanged.emit()
        self._set_screen("transfer_review")

    @Slot(int, object, str, str, str)
    def _accept_maximum_transfer(
        self,
        generation: int,
        result: object,
        network_id: str,
        asset_id: str,
        recipient: str,
    ) -> None:
        if generation != self._maximum_generation or self._closed:
            return
        self._maximum_quoting = False
        if isinstance(result, TransferPreflightError):
            self._set_transfer_error(_transfer_error_message(result.code))
            self.transferChanged.emit()
            return
        if type(result) is not int or result <= 0:
            self._set_transfer_error("Maximum amount is unavailable")
            self.transferChanged.emit()
            return
        try:
            route = transfer_route(network_id, asset_id)
            maximum = self._mainnet_executor.policy.maximum_draft_amount(
                network_id, asset_id, result,
            )
        except TransferPreflightError:
            maximum = None
        if maximum is None:
            self._set_transfer_error("Maximum amount is unavailable")
            self.transferChanged.emit()
            return
        self._set_transfer_error("")
        self.transferChanged.emit()
        self.transferMaximumReady.emit(
            network_id,
            asset_id,
            recipient,
            format_atomic_amount(maximum, route.decimals),
        )

    def _cancel_transfer_request(self, clear_recipient: bool) -> None:
        changed = (
            self._transfer_preparing
            or self._maximum_quoting
            or self._mainnet_in_progress
            or self._transfer_flow.pending is not None
            or self._transfer_flow.current is not None
            or bool(self._transfer_error)
            or (
                clear_recipient
                and any((
                    self._transfer_network,
                    self._transfer_asset,
                    self._transfer_recipient,
                    self._transfer_amount_input,
                ))
            )
        )
        self._transfer_generation += 1
        self._maximum_generation += 1
        self._transfer_expiry_timer.stop()
        self._transfer_flow.close()
        self._flow_price_snapshot = None
        self._transfer_preparing = False
        self._maximum_quoting = False
        self._mainnet_in_progress = False
        self._transfer_error = ""
        if clear_recipient:
            self._transfer_network = ""
            self._transfer_asset = ""
            self._transfer_recipient = ""
            self._transfer_amount_input = ""
        if changed:
            self.transferChanged.emit()

    def _expire_transfer(self) -> None:
        self._transfer_expiry_timer.stop()
        action = self._transfer_flow.current
        if not self._transfer_flow.is_expired():
            action = self._transfer_flow.current
            if action is not None:
                remaining_ms = max(
                    1,
                    int((action.expires_at - datetime.now(UTC)).total_seconds() * 1000) + 1,
                )
                self._transfer_expiry_timer.start(remaining_ms)
            return
        if action is not None and self._current_screen in {
            "sign_transfer", "transfer_result",
        }:
            self._show_mainnet_failure(action, MainnetTransferCode.ACTION_EXPIRED)
            return
        self._transfer_preparing = False
        self._set_transfer_error("Transaction preparation expired")
        self.transferChanged.emit()
        self._set_screen("send")

    def _show_mainnet_failure(
        self, action: PreparedTransferAction, code: MainnetTransferCode,
    ) -> None:
        self._transfer_generation += 1
        self._transfer_expiry_timer.stop()
        self._transfer_flow.close()
        self._transfer_preparing = False
        self._mainnet_in_progress = False
        self._mainnet_result = self._safe_mainnet_result(action, code)
        self.transferChanged.emit()
        self._set_screen("transfer_result")

    @staticmethod
    def _safe_mainnet_result(
        action: PreparedTransferAction, code: MainnetTransferCode,
    ) -> MainnetTransferResult:
        timestamp = (
            datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        )
        return MainnetTransferResult(
            code,
            action.action_id,
            action.digest,
            "",
            "",
            None,
            timestamp,
            False,
            True,
            action.simulation,
        )

    def _clear_mainnet_result(self) -> None:
        if self._mainnet_result is not None:
            self._mainnet_result = None
            self.transferChanged.emit()

    def _start_receipt_check(self, action_id: str, track: bool) -> bool:
        if self._closed or self._receipt_checking:
            return False
        self._receipt_cancelled.set()
        self._receipt_cancelled = Event()
        self._receipt_generation += 1
        generation = self._receipt_generation
        self._receipt_checking = True
        self.transferChanged.emit()
        operation = (
            self._receipt_tracker.track if track
            else self._receipt_tracker.check_once
        )
        arguments = (
            (action_id, self._receipt_cancelled) if track else (action_id,)
        )
        future = self._receipt_executor.submit(operation, *arguments)
        future.add_done_callback(
            lambda completed, current=generation: self._receipt_finished(
                current,
                completed,
            ),
        )
        return True

    def _set_transfer_error(self, message: str) -> None:
        if message != self._transfer_error:
            self._transfer_error = message
            self.transferChanged.emit()

    def _selected_network_ids(self) -> tuple[str, ...]:
        if self._selected_network == "all":
            return tuple(spec.network_id for spec in NETWORKS)
        return (self._selected_network,)

    def _public_data_finished(
        self, generation: int, future: Future[object],
    ) -> None:
        if self._closed:
            return
        try:
            snapshot: object = future.result()
        except Exception:
            snapshot = None
        self._publicDataReady.emit(generation, snapshot)

    def _refresh_public_bundle(
        self,
        profile_id: str,
        address: str,
        network_ids: tuple[str, ...],
    ) -> tuple[PortfolioSnapshot, PriceSnapshot]:
        portfolio = self._public_data_service.refresh(
            profile_id, address, network_ids,
        )
        prices = self._price_service.refresh()
        return portfolio, prices

    @Slot(int, object)
    def _accept_public_data(
        self, generation: int, result: object,
    ) -> None:
        if generation != self._public_data_generation or self._closed:
            return
        active = self._state.active_profile
        requested = self._selected_network_ids()
        snapshot: PortfolioSnapshot | None = None
        prices: PriceSnapshot | None = None
        if (
            isinstance(result, tuple)
            and len(result) == 2
            and isinstance(result[0], PortfolioSnapshot)
            and isinstance(result[1], PriceSnapshot)
        ):
            snapshot, prices = result
        elif isinstance(result, PortfolioSnapshot):
            snapshot = result
        if (
            snapshot is None
            or active is None
            or snapshot.profile_id != active.profile_id
            or snapshot.address != active.address
        ):
            for network_id in requested:
                self._network_snapshots[network_id] = NetworkSnapshot.unavailable(
                    NETWORK_BY_ID[network_id], "RPC_UNAVAILABLE",
                )
            self._price_snapshot = PriceSnapshot.unavailable(
                int(datetime.now(UTC).timestamp()), "RPC_UNAVAILABLE",
            )
        else:
            returned_ids = {item.network_id for item in snapshot.networks}
            if returned_ids != set(requested):
                for network_id in requested:
                    self._network_snapshots[network_id] = NetworkSnapshot.unavailable(
                        NETWORK_BY_ID[network_id], "DATA_INVALID",
                    )
                self._price_snapshot = PriceSnapshot.unavailable(
                    int(datetime.now(UTC).timestamp()), "DATA_INVALID",
                )
            else:
                for item in snapshot.networks:
                    self._network_snapshots[item.network_id] = item
                if prices is not None and prices.chain_id == 8453:
                    self._price_snapshot = prices
                else:
                    self._price_snapshot = PriceSnapshot.unavailable(
                        int(datetime.now(UTC).timestamp()), "DATA_INVALID",
                    )
        self._public_data_refreshing = False
        timestamps = [
            item.updated_at for item in self._network_snapshots.values()
            if item.updated_at
        ]
        self._public_data_updated_text = (
            f"Updated {_display_local_time(max(timestamps))}" if timestamps
            else "Refresh unavailable"
        )
        self.publicDataChanged.emit()

    def _clear_clipboard(self) -> None:
        self._clipboard_timer.stop()
        if self._copied_phrase is not None:
            clipboard = QGuiApplication.clipboard()
            if clipboard.text() == self._copied_phrase:
                clipboard.clear()
        self._copied_phrase = None

    def _cancel_recovery_action(self, *, clear_clipboard: bool) -> None:
        self._recovery_flow.cancel()
        self._recovery_reveal_timer.stop()
        self._recovery_reveal_seconds = 0
        self._recovery_reveal_kind = ""
        self._recovery_reveal_derivation_path = ""
        if self._recovery_display is not None:
            self._recovery_display.clear_material()
        if clear_clipboard:
            self._clear_recovery_clipboard()
        self.recoveryChanged.emit()

    def _tick_recovery_clipboard(self) -> None:
        if self._recovery_clipboard_seconds <= 1:
            self._clear_recovery_clipboard()
            return
        self._recovery_clipboard_seconds -= 1
        self.recoveryChanged.emit()

    def _clear_recovery_clipboard(self) -> None:
        self._recovery_clipboard_timer.stop()
        expected = self._recovery_clipboard_digest
        self._recovery_clipboard_digest = None
        self._recovery_clipboard_seconds = 0
        if expected is not None:
            clipboard = QGuiApplication.clipboard()
            current = clipboard.text()
            current_digest = hashlib.sha256(current.encode("utf-8")).digest()
            del current
            if current_digest == expected:
                clipboard.clear()
        self.recoveryChanged.emit()

    def _tick_recovery_reveal(self) -> None:
        if self._recovery_reveal_seconds <= 1:
            self._cancel_recovery_action(clear_clipboard=False)
            self._settings_section = "security"
            self.settingsSectionChanged.emit()
            self._set_error("Recovery material was hidden after 60 seconds")
            self._set_screen("settings_info")
            return
        self._recovery_reveal_seconds -= 1
        self.recoveryChanged.emit()

    def _set_error(self, message: str) -> None:
        if message != self._error_message:
            self._error_message = message
            self.errorMessageChanged.emit()

    def _set_screen(self, screen: str) -> None:
        if screen != self._current_screen:
            self._current_screen = screen
            self.currentScreenChanged.emit()
            if screen == "main" and self._state.active_profile is not None:
                self.refreshPublicData()


def _recovery_value(
    record: ProfileRecord,
    action: PreparedRecoveryAction,
) -> str:
    summary = record.summary
    if (
        summary.profile_id != action.profile_id
        or summary.label != action.account_label
        or summary.address != action.address
        or summary.profile_type != action.profile_type
    ):
        raise VaultValidationError("Recovery profile changed")
    if action.material_kind is RecoveryMaterialKind.SEED_PHRASE:
        if record.secret.profile_type != MNEMONIC_PROFILE:
            raise VaultValidationError("Seed phrase is unavailable")
        return record.secret.value
    private_key = bytearray(private_key_bytes(record.secret))
    try:
        return "0x" + private_key.hex()
    finally:
        for index in range(len(private_key)):
            private_key[index] = 0
        del private_key

def _display_local_time(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    local = parsed.astimezone()
    return QLocale.system().toString(
        QTime(local.hour, local.minute), QLocale.FormatType.ShortFormat,
    )


def _history_record(action: PreparedTransferAction) -> WalletHistoryRecord:
    created_at = _utc_timestamp(action.created_at)
    return WalletHistoryRecord(
        action_id=action.action_id,
        profile_id=action.profile_id,
        action_type="transfer",
        network=action.network_id,
        chain_id=action.chain_id,
        sender=action.sender,
        recipient=action.recipient,
        contract=action.token_contract,
        token=action.token,
        amount_atomic=str(action.amount_atomic),
        decimals=action.decimals,
        transaction_hash=None,
        status=HistoryStatus.PREPARED,
        created_at=created_at,
        updated_at=created_at,
        simulated=action.simulation,
        max_total_fee_wei=str(action.max_total_fee_wei),
    )


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _transfer_error_message(code: TransferPreflightCode) -> str:
    return {
        TransferPreflightCode.INVALID_ROUTE: "Select Ethereum or Base and ETH or USDC",
        TransferPreflightCode.INVALID_AMOUNT: "Enter an exact positive transfer amount",
        TransferPreflightCode.AMOUNT_LIMIT_EXCEEDED: "Amount exceeds the local route limit",
        TransferPreflightCode.INVALID_RECIPIENT: "Enter a valid EVM recipient address",
        TransferPreflightCode.RESERVED_RECIPIENT: "This recipient address is not allowed",
        TransferPreflightCode.WRONG_CHAIN: "Selected network verification failed",
        TransferPreflightCode.TOKEN_METADATA_INVALID: "USDC contract verification failed",
        TransferPreflightCode.INSUFFICIENT_USDC: "Insufficient USDC for this transfer",
        TransferPreflightCode.INSUFFICIENT_ETH: "Insufficient ETH for amount and maximum fee",
        TransferPreflightCode.GAS_ESTIMATE_FAILED: "Network fee estimation failed",
        TransferPreflightCode.DATA_INVALID: "Selected network returned invalid transaction data",
        TransferPreflightCode.RPC_UNAVAILABLE: "Selected network data is unavailable",
    }[code]
