"""Secret-conscious QObject bridge for Wallet vault and QML flows."""

from __future__ import annotations

from PySide6.QtCore import Property, QObject, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication

from .authority import ActionOutcome, CriticalActionCoordinator, PreparedMockAction
from .model import ProfileSummary, WalletShellState
from .settings import SettingsStore
from .storage import StorageError
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
    mockActionChanged = Signal()
    actionResultChanged = Signal()

    def __init__(self, repository: VaultRepository | None = None) -> None:
        super().__init__()
        self._repository = repository or VaultRepository()
        self._settings = SettingsStore(self._repository.paths)
        self._state = WalletShellState()
        self._current_screen = "welcome"
        self._flow = "none"
        self._error_message = ""
        self._pending_record: ProfileRecord | None = None
        self._pending_vault: PreparedVault | None = None
        self._backup_words: tuple[str, ...] = ()
        self._authority = CriticalActionCoordinator()
        self._mock_action_digest = ""
        self._action_result_title = ""
        self._action_result_message = ""
        self._action_result_success = False
        self._copied_phrase: str | None = None
        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setSingleShot(True)
        self._clipboard_timer.setInterval(60_000)
        self._clipboard_timer.timeout.connect(self._clear_clipboard)
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

    @Property("QVariantMap", notify=mockActionChanged)
    def mockAction(self) -> dict[str, object]:
        action = self._authority.current
        if action is None:
            return {}
        return _mock_action_map(action)

    @Property(str, notify=actionResultChanged)
    def actionResultTitle(self) -> str:
        return self._action_result_title

    @Property(str, notify=actionResultChanged)
    def actionResultMessage(self) -> str:
        return self._action_result_message

    @Property(bool, notify=actionResultChanged)
    def actionResultSuccess(self) -> bool:
        return self._action_result_success

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

    @Slot(result=bool)
    def beginMockAction(self) -> bool:
        active = self._state.active_profile
        if active is None:
            return False
        self._set_error("")
        try:
            action = self._authority.prepare(active)
        except RuntimeError:
            return False
        self._mock_action_digest = action.digest
        self.mockActionChanged.emit()
        self._set_screen("mock_review")
        return True

    @Slot(result=bool)
    def continueMockAction(self) -> bool:
        action = self._authority.current
        if action is None:
            return False
        outcome = self._authority.preflight(action.action_id, self._mock_action_digest)
        if outcome is not None:
            self._finish_mock_action(outcome)
            return False
        self._set_screen("mock_password")
        return True

    @Slot(str, result=bool)
    def submitMockPassword(self, password: str) -> bool:
        action = self._authority.current
        if action is None:
            return False
        outcome = self._authority.preflight(action.action_id, self._mock_action_digest)
        if outcome is not None:
            self._finish_mock_action(outcome)
            return False
        try:
            profiles = self._repository.authenticate(password)
        except AuthenticationFailedError:
            outcome = self._authority.authentication_failed(action.action_id)
            self._finish_mock_action(outcome)
            return False
        except VaultUnavailableError:
            self._authority.fail()
            self._clear_mock_action()
            self._set_screen("unavailable")
            return False
        except (StorageError, VaultValidationError):
            self._finish_mock_action(self._authority.fail())
            return False
        authenticated = next(
            (profile for profile in profiles if profile.profile_id == action.profile_id), None,
        )
        if (
            authenticated is None
            or authenticated.address != action.sender
            or authenticated.label != action.account_label
            or self._state.active_profile_id != action.profile_id
        ):
            self._finish_mock_action(self._authority.fail())
            return False
        outcome = self._authority.authorize_and_consume(
            action.action_id, self._mock_action_digest, self._consume_simulation,
        )
        self._finish_mock_action(outcome)
        return outcome is ActionOutcome.AUTHORIZED

    @Slot()
    def rejectMockAction(self) -> None:
        self._authority.reject()
        self._clear_mock_action()
        self._set_screen("main")

    @Slot()
    def cancelMockAction(self) -> None:
        self._authority.cancel()
        self._clear_mock_action()
        self._set_screen("main")

    @Slot()
    def finishMockResult(self) -> None:
        self._clear_action_result()
        self._set_screen("main")

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
            invalidated = self._authority.profile_changed(profile_id)
            self.activeProfileChanged.emit()
            if invalidated:
                self._clear_mock_action()
                self._set_screen("main")
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
    def shutdown(self) -> None:
        self._authority.close()
        self._clear_mock_action()
        self._clear_sensitive()

    def _initialize(self) -> None:
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
        self._authority.close()
        self._clear_mock_action()
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

    @staticmethod
    def _consume_simulation(_action: PreparedMockAction) -> None:
        return

    def _finish_mock_action(self, outcome: ActionOutcome) -> None:
        results = {
            ActionOutcome.AUTHORIZED: (
                "Simulation authorized",
                "Authorization was used once. No transaction was signed or sent.",
                True,
            ),
            ActionOutcome.AUTHENTICATION_FAILED: (
                "Authentication failed",
                "The action was closed. No transaction was signed or sent.",
                False,
            ),
            ActionOutcome.EXPIRED: (
                "Action expired",
                "Prepare a new simulation. No transaction was signed or sent.",
                False,
            ),
        }
        title, message, success = results.get(
            outcome,
            (
                "Authorization unavailable",
                "The action was closed. No transaction was signed or sent.",
                False,
            ),
        )
        self._action_result_title = title
        self._action_result_message = message
        self._action_result_success = success
        self.actionResultChanged.emit()
        self._clear_mock_action()
        self._set_screen("mock_result")

    def _clear_mock_action(self) -> None:
        if self._mock_action_digest or self._authority.current is not None:
            self._mock_action_digest = ""
            self.mockActionChanged.emit()

    def _clear_action_result(self) -> None:
        if self._action_result_title or self._action_result_message:
            self._action_result_title = ""
            self._action_result_message = ""
            self._action_result_success = False
            self.actionResultChanged.emit()

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


def _mock_action_map(action: PreparedMockAction) -> dict[str, object]:
    return {
        "actionId": action.action_id,
        "shortActionId": f"{action.action_id[:12]}…",
        "accountLabel": action.account_label,
        "sender": action.sender,
        "shortSender": f"{action.sender[:8]}…{action.sender[-6:]}",
        "network": action.network,
        "chainId": action.chain_id,
        "token": action.token,
        "amount": action.amount_display,
        "recipient": action.recipient,
        "feeStatus": action.fee_status,
        "expiresAt": action.expires_at.strftime("%H:%M:%S UTC"),
    }
