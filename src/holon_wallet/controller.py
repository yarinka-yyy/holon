"""Secret-conscious QObject bridge for Wallet vault and QML flows."""

from __future__ import annotations

from concurrent.futures import Executor, Future, ThreadPoolExecutor
from datetime import UTC, datetime

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

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
from .settings import SettingsStore
from .storage import StorageError
from .transfer import (
    PreparedTransferAction,
    TransferFlowCoordinator,
    TransferPreflightCode,
    TransferPreflightError,
    TransferPreflightService,
    normalize_recipient,
    transfer_action_to_map,
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
    publicDataChanged = Signal()
    selectedNetworkChanged = Signal()
    historyChanged = Signal()
    _publicDataReady = Signal(int, object)
    _transferReady = Signal(int, object)

    def __init__(
        self,
        repository: VaultRepository | None = None,
        public_data_service: PublicDataService | None = None,
        history_store: HistoryStore | None = None,
        public_data_executor: Executor | None = None,
        transfer_preflight_service: TransferPreflightService | None = None,
        transfer_executor: Executor | None = None,
    ) -> None:
        super().__init__()
        self._repository = repository or VaultRepository()
        self._settings = SettingsStore(self._repository.paths)
        self._public_data_service = public_data_service or PublicDataService()
        self._history_store = history_store or HistoryStore(self._repository.paths)
        self._public_data_executor = public_data_executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="holon-public-read",
        )
        self._owns_public_data_executor = public_data_executor is None
        self._transfer_preflight_service = (
            transfer_preflight_service or TransferPreflightService()
        )
        self._transfer_executor = transfer_executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="holon-transfer-preflight",
        )
        self._owns_transfer_executor = transfer_executor is None
        self._state = WalletShellState()
        self._current_screen = "welcome"
        self._flow = "none"
        self._error_message = ""
        self._pending_record: ProfileRecord | None = None
        self._pending_vault: PreparedVault | None = None
        self._backup_words: tuple[str, ...] = ()
        self._transfer_flow = TransferFlowCoordinator()
        self._transfer_generation = 0
        self._transfer_preparing = False
        self._transfer_error = ""
        self._transfer_recipient = ""
        self._selected_network = "all"
        self._network_snapshots = {
            spec.network_id: NetworkSnapshot.unavailable(spec, "NOT_REFRESHED")
            for spec in NETWORKS
        }
        self._public_data_refreshing = False
        self._public_data_generation = 0
        self._public_data_updated_text = "Not refreshed"
        self._history_records: tuple[WalletHistoryRecord, ...] = ()
        self._history_available = True
        self._closed = False
        self._copied_phrase: str | None = None
        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setSingleShot(True)
        self._clipboard_timer.setInterval(60_000)
        self._clipboard_timer.timeout.connect(self._clear_clipboard)
        self._transfer_expiry_timer = QTimer(self)
        self._transfer_expiry_timer.setSingleShot(True)
        self._transfer_expiry_timer.timeout.connect(self._expire_transfer)
        self._publicDataReady.connect(self._accept_public_data)
        self._transferReady.connect(self._accept_transfer_preflight)
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

    @Property(str, notify=transferChanged)
    def transferError(self) -> str:
        return self._transfer_error

    @Property(str, notify=transferChanged)
    def transferRecipient(self) -> str:
        return self._transfer_recipient

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
        if not self._state.profiles:
            return
        self._cancel_transfer_request(clear_recipient=True)
        self._set_screen("send")

    @Slot(str, result=bool)
    def prepareTransfer(self, recipient: str) -> bool:
        active = self._state.active_profile
        if active is None or self._closed or self._transfer_preparing:
            return False
        self._set_transfer_error("")
        try:
            normalized = normalize_recipient(recipient, active.address)
            request = self._transfer_flow.begin(active.profile_id)
        except TransferPreflightError as error:
            self._set_transfer_error(_transfer_error_message(error.code))
            return False
        except RuntimeError:
            return False
        self._transfer_recipient = normalized
        self._transfer_preparing = True
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

    @Slot(result=str)
    def pasteTransferRecipient(self) -> str:
        return QGuiApplication.clipboard().text()

    @Slot()
    def cancelTransfer(self) -> None:
        self._cancel_transfer_request(clear_recipient=True)
        self._set_screen("main")

    @Slot()
    def editTransfer(self) -> None:
        action = self._transfer_flow.current
        if action is None:
            self._cancel_transfer_request(clear_recipient=False)
            self._set_screen("send")
            return
        self._transfer_recipient = action.recipient
        self._cancel_transfer_request(clear_recipient=False)
        self._set_screen("send")

    @Slot()
    def finishTransfer(self) -> None:
        self._cancel_transfer_request(clear_recipient=True)
        self._set_screen("main")

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
            self._public_data_service.refresh,
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
        if not any(profile.profile_id == profile_id for profile in self._state.profiles):
            return False
        try:
            self._settings.save_active_id(profile_id)
        except StorageError:
            self._set_error("Active Account could not be saved")
            return False
        if self._state.select_profile(profile_id):
            invalidated = self._transfer_flow.profile_changed(profile_id)
            self.activeProfileChanged.emit()
            self.historyChanged.emit()
            if invalidated:
                self._transfer_generation += 1
                self._transfer_preparing = False
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
        if self._state.profiles:
            self._set_screen("main")

    @Slot()
    def showWallets(self) -> None:
        if self._state.profiles:
            self._set_screen("wallets")

    @Slot()
    def showHistory(self) -> None:
        if self._state.profiles:
            self._load_history()
            self._set_screen("history")

    @Slot()
    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._public_data_generation += 1
        self._cancel_transfer_request(clear_recipient=True)
        self._clear_sensitive()
        if self._owns_public_data_executor:
            self._public_data_executor.shutdown(wait=False, cancel_futures=True)
        if self._owns_transfer_executor:
            self._transfer_executor.shutdown(wait=False, cancel_futures=True)

    def _initialize(self) -> None:
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
        self.historyChanged.emit()
        remaining_ms = max(
            1,
            int((result.expires_at - datetime.now(UTC)).total_seconds() * 1000) + 1,
        )
        self._transfer_expiry_timer.start(remaining_ms)
        self._set_transfer_error("")
        self.transferChanged.emit()
        self._set_screen("transfer_review")

    def _cancel_transfer_request(self, clear_recipient: bool) -> None:
        changed = (
            self._transfer_preparing
            or self._transfer_flow.pending is not None
            or self._transfer_flow.current is not None
            or bool(self._transfer_error)
            or (clear_recipient and bool(self._transfer_recipient))
        )
        self._transfer_generation += 1
        self._transfer_expiry_timer.stop()
        self._transfer_flow.close()
        self._transfer_preparing = False
        self._transfer_error = ""
        if clear_recipient:
            self._transfer_recipient = ""
        if changed:
            self.transferChanged.emit()

    def _expire_transfer(self) -> None:
        self._transfer_expiry_timer.stop()
        if not self._transfer_flow.is_expired():
            action = self._transfer_flow.current
            if action is not None:
                remaining_ms = max(
                    1,
                    int((action.expires_at - datetime.now(UTC)).total_seconds() * 1000) + 1,
                )
                self._transfer_expiry_timer.start(remaining_ms)
            return
        self._transfer_preparing = False
        self._set_transfer_error("Transaction preparation expired")
        self.transferChanged.emit()
        self._set_screen("send")

    def _set_transfer_error(self, message: str) -> None:
        if message != self._transfer_error:
            self._transfer_error = message
            self.transferChanged.emit()

    def _selected_network_ids(self) -> tuple[str, ...]:
        if self._selected_network == "all":
            return tuple(spec.network_id for spec in NETWORKS)
        return (self._selected_network,)

    def _public_data_finished(
        self, generation: int, future: Future[PortfolioSnapshot],
    ) -> None:
        if self._closed:
            return
        try:
            snapshot: PortfolioSnapshot | None = future.result()
        except Exception:
            snapshot = None
        self._publicDataReady.emit(generation, snapshot)

    @Slot(int, object)
    def _accept_public_data(
        self, generation: int, snapshot: PortfolioSnapshot | None,
    ) -> None:
        if generation != self._public_data_generation or self._closed:
            return
        active = self._state.active_profile
        requested = self._selected_network_ids()
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
        else:
            returned_ids = {item.network_id for item in snapshot.networks}
            if returned_ids != set(requested):
                for network_id in requested:
                    self._network_snapshots[network_id] = NetworkSnapshot.unavailable(
                        NETWORK_BY_ID[network_id], "DATA_INVALID",
                    )
            else:
                for item in snapshot.networks:
                    self._network_snapshots[item.network_id] = item
        self._public_data_refreshing = False
        timestamps = [
            item.updated_at for item in self._network_snapshots.values()
            if item.updated_at
        ]
        self._public_data_updated_text = (
            f"Updated {_display_utc(max(timestamps))}" if timestamps
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

def _display_utc(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    return parsed.strftime("%H:%M UTC")


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
    )


def _utc_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _transfer_error_message(code: TransferPreflightCode) -> str:
    return {
        TransferPreflightCode.INVALID_RECIPIENT: "Enter a valid EVM recipient address",
        TransferPreflightCode.RESERVED_RECIPIENT: "This recipient address is not allowed",
        TransferPreflightCode.WRONG_CHAIN: "Base network verification failed",
        TransferPreflightCode.TOKEN_METADATA_INVALID: "USDC contract verification failed",
        TransferPreflightCode.INSUFFICIENT_USDC: "This Account does not have 1 USDC on Base",
        TransferPreflightCode.INSUFFICIENT_ETH: "Not enough ETH on Base for the maximum fee",
        TransferPreflightCode.GAS_ESTIMATE_FAILED: "Network fee estimation failed",
        TransferPreflightCode.DATA_INVALID: "Base returned invalid transaction data",
        TransferPreflightCode.RPC_UNAVAILABLE: "Base network data is unavailable",
    }[code]
