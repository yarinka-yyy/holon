"""Secret-conscious QObject bridge for Wallet vault and QML flows."""

from __future__ import annotations

import hashlib
import hmac
import json
from concurrent.futures import Executor, Future, ThreadPoolExecutor
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event
from typing import Callable

from PySide6.QtCore import Property, QLocale, QObject, QTime, QTimer, Signal, Slot
from PySide6.QtGui import QGuiApplication
from holon_wallet_control import AUTHORITY_VERSION
from holon_guard_ipc.policy_control import ControlProtocolError, ControlUnavailable
from holon_policy import Policy, policy_digest
from holon_lending import (
    ActionProfilesState, LendingPreflightError, LendingPreflightService,
    parse_lending_intent,
)

from .approval import (
    AllowanceReadService,
    AllowanceSnapshot,
    PreparedRevokeAction,
    RevokeFlowCoordinator,
    RevokeFlowError,
    RevokeFlowState,
    RevokePolicyCode,
    RevokePolicy,
    RevokePreflightCode,
    RevokePreflightError,
    RevokePreflightService,
    allowance_snapshot_to_map,
    revoke_action_to_map,
)
from .broadcast import (
    BroadcastReceiptTracker,
    MainnetBroadcastPolicy,
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
from .lending_action import prepare_lending_action
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
from .public_cache import PublicCacheStore
from .prices import (
    PriceService,
    PriceSnapshot,
    PriceStatus,
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
from .trusted_recipients import (
    ROUTE_ORDER,
    TrustedDraftError,
    TrustedDraftUnavailable,
    TrustedPolicyDraft,
    TrustedPolicyDraftStore,
    TrustedRecipientDraft,
    TrustedRouteDraft,
    parse_cap,
    parse_fee_cap,
    validate_draft_address,
    validate_label,
    trusted_draft_digest,
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
    trustedDraftChanged = Signal()
    recoveryChanged = Signal()
    approvalChanged = Signal()
    guardNoticeChanged = Signal()
    _publicDataReady = Signal(int, object)
    _transferReady = Signal(int, object)
    _maximumReady = Signal(int, object, str, str, str)
    _mainnetReady = Signal(int, object)
    _receiptReady = Signal(int, object)
    _approvalReady = Signal(int, object)
    _revokeReady = Signal(int, object)
    _revokeExecutionReady = Signal(int, object)

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
        allowance_service: AllowanceReadService | None = None,
        revoke_preflight_service: RevokePreflightService | None = None,
        transfer_policy: MainnetBroadcastPolicy | None = None,
        policy_control_client=None,
        lending_preflight_service: LendingPreflightService | None = None,
        public_cache_store: PublicCacheStore | None = None,
    ) -> None:
        super().__init__()
        self._repository = repository or VaultRepository()
        self._settings = SettingsStore(self._repository.paths)
        self._trusted_store = TrustedPolicyDraftStore(self._repository.paths)
        self._policy_control_client = policy_control_client
        self._public_data_service = public_data_service or PublicDataService()
        self._price_service = price_service or PriceService()
        self._history_store = history_store or HistoryStore(self._repository.paths)
        self._public_cache_store = (
            public_cache_store or PublicCacheStore(self._repository.paths)
        )
        self._public_data_executor = public_data_executor or ThreadPoolExecutor(
            max_workers=2, thread_name_prefix="holon-public-read",
        )
        self._owns_public_data_executor = public_data_executor is None
        self._transfer_preflight_service = (
            transfer_preflight_service or TransferPreflightService()
        )
        self._lending_action_profiles = ActionProfilesState.load()
        self._lending_preflight_service = (
            lending_preflight_service
            or LendingPreflightService(self._lending_action_profiles)
        )
        self._transfer_executor = transfer_executor or ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="holon-critical-transfer",
        )
        self._owns_transfer_executor = transfer_executor is None
        self._mainnet_executor = mainnet_executor or MainnetTransferExecutor(
            self._repository,
            self._history_store,
            policy=transfer_policy,
        )
        revoke_policy = getattr(
            self._mainnet_executor, "revoke_policy", RevokePolicy.from_environment(),
        )
        self._revoke_policy = revoke_policy
        revoke_environ = getattr(self._mainnet_executor, "_environ", None)
        revoke_rpc_factory = getattr(self._mainnet_executor, "_rpc_factory", None)
        self._allowance_service = allowance_service or AllowanceReadService(
            revoke_policy, revoke_rpc_factory, revoke_environ,
        )
        self._revoke_preflight_service = (
            revoke_preflight_service or RevokePreflightService(
                revoke_policy, revoke_rpc_factory, revoke_environ,
            )
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
        self._external_transfer: dict[str, object] | None = None
        self._external_completion: Callable[[dict[str, object]], None] | None = None
        self._guard_status_sender: Callable[[dict[str, object]], None] | None = None
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
        self._cached_network_ids: set[str] = set()
        self._price_cached = False
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
        self._trusted_saved = TrustedPolicyDraft()
        self._trusted_working = TrustedPolicyDraft()
        self._trusted_available = True
        self._trusted_status = ""
        self._trusted_route_key: tuple[str, str] | None = None
        self._trusted_recipient_address: str | None = None
        self._trusted_review_digest = ""
        self._trusted_apply_review_digest = ""
        self._trusted_apply_policy_digest = ""
        self._trusted_active_revision = 0
        self._trusted_active_digest = ""
        self._trusted_active_source_digest: str | None = None
        self._trusted_transfer_enabled = False
        self._trusted_lending_enabled = False
        self._trusted_authority_state = "INVALID"
        self._trusted_apply_expected_revision = 0
        self._trusted_apply_expected_digest = ""
        self._trusted_policy_operation = ""
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
        self._revoke_flow = RevokeFlowCoordinator()
        self._allowance_snapshots: tuple[AllowanceSnapshot, ...] = ()
        self._approval_refreshing = False
        self._approval_preparing = False
        self._approval_error = ""
        self._approval_generation = 0
        self._guard_open_notice = ""
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
        self._revoke_expiry_timer = QTimer(self)
        self._revoke_expiry_timer.setSingleShot(True)
        self._revoke_expiry_timer.timeout.connect(self._expire_revoke)
        self._guard_notice_timer = QTimer(self)
        self._guard_notice_timer.setSingleShot(True)
        self._guard_notice_timer.setInterval(6_000)
        self._guard_notice_timer.timeout.connect(self._clear_guard_notice)
        self._publicDataReady.connect(self._accept_public_data)
        self._transferReady.connect(self._accept_transfer_preflight)
        self._maximumReady.connect(self._accept_maximum_transfer)
        self._mainnetReady.connect(self._accept_mainnet_result)
        self._receiptReady.connect(self._accept_receipt_result)
        self._approvalReady.connect(self._accept_allowances)
        self._revokeReady.connect(self._accept_revoke_preflight)
        self._revokeExecutionReady.connect(self._accept_revoke_result)
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

    @Property(str, notify=guardNoticeChanged)
    def guardOpenNotice(self) -> str:
        return self._guard_open_notice

    @Property(str, notify=errorMessageChanged)
    def errorMessage(self) -> str:
        return self._error_message

    @Property("QVariantList", notify=backupWordsChanged)
    def backupWords(self) -> list[str]:
        return list(self._backup_words)

    @Property(str, notify=flowChanged)
    def passwordTitle(self) -> str:
        return {
            "create": "Set Password",
            "first_import": "Set Password",
            "add_private": "Confirm Password",
        }.get(self._flow, "Enter Password")

    @Property(str, notify=flowChanged)
    def passwordSubtitle(self) -> str:
        return {
            "create": "4 characters min · longer is safer",
            "first_import": "4 characters min · longer is safer",
            "add_private": "Fresh authentication is required",
        }.get(self._flow, "")

    @Property(str, notify=flowChanged)
    def passwordActionLabel(self) -> str:
        return "Confirm"

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

    @Slot(str, str, str, result=str)
    def maximumTransferAmount(
        self, network_id: str, asset_id: str, recipient: str,
    ) -> str:
        try:
            route = transfer_route(network_id, asset_id)
            active = self._state.active_profile
            if active is None:
                return ""
            normalized = normalize_recipient(recipient, active.address)
        except TransferPreflightError:
            return ""
        if asset_id == "eth":
            return ""
        if network_id in self._cached_network_ids:
            return ""
        snapshot = self._network_snapshots.get(network_id)
        if snapshot is None or snapshot.status is PublicDataStatus.UNAVAILABLE:
            return ""
        balance = snapshot.eth if asset_id == "eth" else snapshot.usdc
        if balance is None or balance.decimals != route.decimals:
            return ""
        maximum = self._mainnet_executor.policy.maximum_draft_amount(
            network_id, asset_id, balance.atomic_units, normalized,
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
        subject = "Lending action" if action.action_type == "lending" else "Transfer"
        if code is MainnetTransferCode.POLICY_UNAVAILABLE:
            return f"{subject} policy is unavailable"
        if code is MainnetTransferCode.POLICY_AUTHORITY_DISABLED:
            return f"{subject} is disabled by local policy"
        if code is MainnetTransferCode.POLICY_VERSION_MISMATCH:
            return "Transfer policy version does not match"
        if code is MainnetTransferCode.NETWORK_NOT_ALLOWED:
            return "Network is not allowed by local policy"
        if code is MainnetTransferCode.ASSET_NOT_ALLOWED:
            return "Asset is not allowed by local policy"
        if code is MainnetTransferCode.RECIPIENT_NOT_ALLOWED:
            return "Recipient is not allowed by local policy"
        if code is MainnetTransferCode.FEE_LIMIT_EXCEEDED:
            return "Maximum fee exceeds the local mainnet limit"
        if code is MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED:
            return "Transfer amount exceeds the local route limit"
        return ""

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
            return (
                "LOCAL WALLET  ·  CACHED DATA  ·  UPDATING"
                if self._cached_network_ids or self._price_cached
                else "LOCAL WALLET  ·  REFRESHING PUBLIC DATA"
            )
        if self._cached_network_ids or self._price_cached:
            return "LOCAL WALLET  ·  CACHED PUBLIC DATA"
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

    @Property("QVariantList", notify=trustedDraftChanged)
    def trustedDraftRoutes(self) -> list[dict[str, object]]:
        return [self._trusted_route_map(item) for item in self._trusted_working.routes]

    @Property("QVariantMap", notify=trustedDraftChanged)
    def trustedLendingLimits(self) -> dict[str, object]:
        amount = self._trusted_working.lending_max_amount_atomic
        fee = self._trusted_working.lending_max_total_fee_wei
        return {
            "configured": amount is not None and fee is not None,
            "amount": "" if amount is None else format_atomic_amount(int(amount), 6),
            "fee": "" if fee is None else format_atomic_amount(int(fee), 18),
            "enabled": self._trusted_lending_enabled,
            "withdrawOnly": self._trusted_working.lending_allowed_actions == ("withdraw",),
        }

    @Property("QVariantMap", notify=trustedDraftChanged)
    def trustedRoute(self) -> dict[str, object]:
        if self._trusted_route_key is not None:
            route = self._trusted_working.route(*self._trusted_route_key)
            if route is not None:
                value = self._trusted_route_map(route)
                value["isNew"] = False
                return value
        return {
            "networkId": "base", "networkLabel": "Base",
            "assetId": "usdc", "assetLabel": "USDC", "chainId": "8453",
            "routeAmount": "", "routeAmountUsd": "Data unavailable",
            "feeAmount": "", "feeUsd": "Data unavailable",
            "recipients": [], "recipientCount": 0, "isNew": True,
        }

    @Property("QVariantMap", notify=trustedDraftChanged)
    def trustedRecipient(self) -> dict[str, object]:
        route = (
            self._trusted_working.route(*self._trusted_route_key)
            if self._trusted_route_key is not None else None
        )
        if route is not None and self._trusted_recipient_address is not None:
            recipient = next((
                item for item in route.recipients
                if item.address.lower() == self._trusted_recipient_address.lower()
            ), None)
            if recipient is not None:
                value = self._trusted_recipient_map(route, recipient)
                value["isNew"] = False
                return value
        return {
            "label": "", "address": "", "shortAddress": "",
            "maxAmount": "", "maxAmountUsd": "Data unavailable", "isNew": True,
        }

    @Property(bool, notify=trustedDraftChanged)
    def trustedDraftAvailable(self) -> bool:
        return self._trusted_available

    @Property(bool, notify=trustedDraftChanged)
    def trustedDraftDirty(self) -> bool:
        return self._trusted_working.canonical() != self._trusted_saved.canonical()

    @Property(str, notify=trustedDraftChanged)
    def trustedDraftStatus(self) -> str:
        return self._trusted_status

    @Property(str, notify=trustedDraftChanged)
    def trustedActiveRevision(self) -> str:
        return (
            "Baseline revision 0"
            if self._trusted_active_revision == 0
            else f"Active revision {self._trusted_active_revision}"
        )

    @Property(bool, notify=trustedDraftChanged)
    def trustedDraftMatchesActive(self) -> bool:
        if not self._trusted_available:
            return False
        try:
            candidate = self._trusted_saved.to_envelope()["policy_digest"]
        except TrustedDraftError:
            return False
        try:
            source = trusted_draft_digest(self._trusted_saved.to_envelope())
        except TrustedDraftError:
            return False
        return candidate == self._trusted_active_digest or source == self._trusted_active_source_digest

    @Property(bool, notify=trustedDraftChanged)
    def trustedCanApply(self) -> bool:
        return (
            self._trusted_available
            and self._trusted_authority_state == "READY"
            and not self.trustedDraftDirty
            and self._policy_control_client is not None
            and not self._critical_flow_active()
        )

    @Property(bool, notify=trustedDraftChanged)
    def trustedCanInitializeAuthority(self) -> bool:
        return bool(
            self._trusted_authority_state == "INITIALIZATION_REQUIRED"
            and self._trusted_active_revision == 0
            and self._policy_control_client is not None
            and not self._critical_flow_active()
        )

    @Property(bool, notify=trustedDraftChanged)
    def trustedCanActivateLending(self) -> bool:
        return bool(
            self.trustedCanApply
            and self.trustedLendingLimits["configured"]
            and self.trustedLendingLimits["withdrawOnly"]
            and self.trustedDraftMatchesActive
            and not self._trusted_lending_enabled
        )

    @Property(bool, notify=trustedDraftChanged)
    def trustedCanDeactivateLending(self) -> bool:
        return bool(
            self._trusted_lending_enabled and self._policy_control_client is not None
            and not self._critical_flow_active()
        )

    @Property(str, notify=trustedDraftChanged)
    def trustedAuthorityStatus(self) -> str:
        return (
            ("Send enabled" if self._trusted_transfer_enabled else "Send disabled")
            + " · "
            + ("Lending enabled" if self._trusted_lending_enabled else "Lending disabled")
        )

    @Property(str, notify=trustedDraftChanged)
    def trustedAuthorityState(self) -> str:
        return {
            "READY": "Authority state ready",
            "INITIALIZATION_REQUIRED": "Authority setup required",
        }.get(self._trusted_authority_state, "Authority state unavailable")

    @Property(bool, notify=currentScreenChanged)
    def trustedApplyMode(self) -> bool:
        return self._current_screen in {"trusted_apply_review", "trusted_apply_password"}

    @Property(str, notify=trustedDraftChanged)
    def trustedPolicyOperation(self) -> str:
        return self._trusted_policy_operation

    @Slot(str, str, result=str)
    def trustedAmountUsd(self, asset: str, amount_input: str) -> str:
        try:
            atomic = int(parse_cap(amount_input, asset))
            spec = transfer_route("base", asset)
        except (TrustedDraftError, TransferPreflightError, ValueError):
            return "Data unavailable"
        return estimate_asset_usd(
            atomic, spec.decimals, asset, self._price_snapshot,
        )

    @Slot(str, result=str)
    def trustedFeeUsd(self, fee_input: str) -> str:
        try:
            atomic = int(parse_fee_cap(fee_input))
        except (TrustedDraftError, ValueError):
            return "Data unavailable"
        return estimate_wei_usd(atomic, self._price_snapshot)

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

    @Property("QVariantList", notify=approvalChanged)
    def approvalRecords(self) -> list[dict[str, object]]:
        policy = self._revoke_policy
        return [allowance_snapshot_to_map(item, policy) for item in self._allowance_snapshots]

    @Property(bool, notify=approvalChanged)
    def approvalRefreshing(self) -> bool:
        return self._approval_refreshing

    @Property(bool, notify=approvalChanged)
    def approvalPreparing(self) -> bool:
        return self._approval_preparing

    @Property(str, notify=approvalChanged)
    def approvalError(self) -> str:
        return self._approval_error

    @Property("QVariantMap", notify=approvalChanged)
    def revokeAction(self) -> dict[str, object]:
        action = self._revoke_flow.current
        return revoke_action_to_map(action) if action is not None else {}

    @Property(bool, notify=approvalChanged)
    def revokeExecutionAvailable(self) -> bool:
        action = self._revoke_flow.current
        return (
            action is not None
            and self._revoke_flow.state is RevokeFlowState.PREPARED
            and not self._mainnet_in_progress
            and self._revoke_policy.evaluate(action) is None
        )

    @Property(str, notify=approvalChanged)
    def revokeFeeLimit(self) -> str:
        action = self._revoke_flow.current
        return (
            self._revoke_policy.fee_display(action.network_id)
            if action is not None else "Not configured"
        )

    @Property(str, notify=approvalChanged)
    def revokeFeeUsd(self) -> str:
        action = self._revoke_flow.current
        if action is None:
            return "Data unavailable"
        return estimate_wei_usd(action.max_total_fee_wei, self._price_snapshot)

    @Property(str, notify=approvalChanged)
    def revokeGateMessage(self) -> str:
        action = self._revoke_flow.current
        if action is None:
            return ""
        code = self._revoke_policy.evaluate(action)
        if code is RevokePolicyCode.POLICY_UNAVAILABLE:
            prefix = action.network_id.upper()
            return (
                f"Configure HOLON_{prefix}_USDC_REVOKE_ENABLED, "
                f"HOLON_{prefix}_USDC_REVOKE_SPENDER and "
                f"HOLON_{prefix}_USDC_REVOKE_MAX_TOTAL_FEE_WEI"
            )
        if code is RevokePolicyCode.FEE_LIMIT_EXCEEDED:
            return "Maximum fee exceeds the local revoke limit"
        return "Fresh password and explicit confirmation authorize one revoke"

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
            "revoke_review": 0,
            "revoke_confirm": 1,
            "revoke_submit": 2,
            "revoke_result": 3,
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
            or self._revoke_flow.current is not None
            or self._revoke_flow.pending is not None
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
                network_id, asset_id, amount_atomic, normalized,
            )
            if amount_code is not None:
                self._set_transfer_error(_mainnet_policy_error_message(amount_code))
                return False
            request = self._transfer_flow.begin(
                active.profile_id, network_id, asset_id, amount_atomic,
                self._mainnet_executor.policy.policy_revision,
                self._mainnet_executor.policy.policy_digest_value,
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

    def attach_guard_status_sender(
        self, sender: Callable[[dict[str, object]], None],
    ) -> None:
        self._guard_status_sender = sender

    def prepareExternalTransfer(
        self,
        request: dict[str, object],
        completion: Callable[[dict[str, object]], None],
    ) -> None:
        active = self._state.active_profile
        busy = (
            active is None
            or self._closed
            or self._flow != "none"
            or self._transfer_preparing
            or self._transfer_flow.pending is not None
            or self._transfer_flow.current is not None
            or self._mainnet_in_progress
            or self._recovery_flow.current is not None
            or self._revoke_flow.current is not None
            or self._revoke_flow.pending is not None
            or self._current_screen in {
                "send", "recovery_reveal", "submit_transfer", "revoke_submit",
            }
        )
        if busy:
            completion(self._external_refusal(request, "WALLET_BUSY"))
            return
        try:
            network_id = str(request["network"])
            asset_id = str(request["asset"])
            route = transfer_route(network_id, asset_id)
            amount_atomic = int(str(request["amount_atomic"]))
            normalized = normalize_recipient(str(request["recipient"]), active.address)
            created_at = datetime.fromisoformat(str(request["created_at"]).replace("Z", "+00:00"))
            expires_at = datetime.fromisoformat(str(request["expires_at"]).replace("Z", "+00:00"))
            if not self._mainnet_executor.policy.matches(
                int(request["policy_revision"]), str(request["policy_digest"]),
            ):
                completion(self._external_refusal(request, "POLICY_REVISION_CHANGED"))
                return
            policy_code = self._mainnet_executor.policy.draft_amount_code(
                network_id,
                asset_id,
                amount_atomic,
                normalized,
                str(request["policy_version"]),
            )
            if policy_code is not None:
                completion(self._external_refusal(request, policy_code.value))
                return
            pending = self._transfer_flow.begin_external(
                str(request["action_id"]), active.profile_id, created_at, expires_at,
                network_id, asset_id, amount_atomic,
                int(request["policy_revision"]), str(request["policy_digest"]),
            )
        except (KeyError, TypeError, ValueError, RuntimeError, TransferPreflightError) as error:
            code = error.code.value if isinstance(error, TransferPreflightError) else "TRANSFER_INTENT_INVALID"
            completion(self._external_refusal(request, code))
            return
        self._transfer_network = network_id
        self._transfer_asset = asset_id
        self._transfer_recipient = normalized
        self._transfer_amount_input = format_atomic_amount(amount_atomic, route.decimals)
        self._external_transfer = dict(request)
        self._external_completion = completion
        self._set_transfer_error("")
        self._transfer_preparing = True
        self._transfer_generation += 1
        generation = self._transfer_generation
        self.transferChanged.emit()
        future = self._transfer_executor.submit(
            self._transfer_preflight_service.prepare, pending, active, normalized,
        )
        future.add_done_callback(
            lambda completed, current=generation: self._transfer_finished(current, completed),
        )

    def prepareExternalLending(
        self,
        request: dict[str, object],
        completion: Callable[[dict[str, object]], None],
    ) -> None:
        active = self._state.active_profile
        if (
            active is None or self._closed or self._flow != "none"
            or self._transfer_flow.pending is not None
            or self._transfer_flow.current is not None or self._mainnet_in_progress
        ):
            completion(self._external_refusal(request, "WALLET_BUSY"))
            return
        try:
            intent = parse_lending_intent({
                "module_id": "lending", "module_version": "1",
                "protocol_profile_id": "aave-v3-base-usdc",
                "protocol_profile_version": "1", "network": "base",
                "asset": "usdc", "beneficiary_mode": "active_wallet_account",
                "action": request["action"], "amount_mode": request["amount_mode"],
                "amount": request["amount"],
            })
            amount_atomic = intent.amount_atomic
            created = datetime.fromisoformat(str(request["created_at"]).replace("Z", "+00:00"))
            expires = datetime.fromisoformat(str(request["expires_at"]).replace("Z", "+00:00"))
            if not self._mainnet_executor.policy.matches(
                int(request["policy_revision"]), str(request["policy_digest"]),
            ):
                raise ValueError("POLICY_REVISION_CHANGED")
            policy_code = self._mainnet_executor.policy.lending_intent_code(
                intent.action, intent.amount_mode, amount_atomic,
                str(request["action_profile_digest"]),
                str(request.get("policy_version", "")),
            )
            if policy_code is not None:
                completion(self._external_refusal(request, policy_code.value))
                return
            pending = self._transfer_flow.begin_external(
                str(request["action_id"]), active.profile_id, created, expires,
                "base", "usdc", amount_atomic, int(request["policy_revision"]),
                str(request["policy_digest"]), intent.amount_mode,
            )
        except Exception as error:
            completion(self._external_refusal(request, str(error) or "LENDING_ACTION_INVALID"))
            return
        self._external_transfer = dict(request)
        self._external_completion = completion
        self._transfer_network = "base"
        self._transfer_asset = "usdc"
        self._transfer_recipient = "Aave V3 Pool"
        self._transfer_amount_input = intent.amount or "All current position"
        self._transfer_preparing = True
        self._transfer_generation += 1
        generation = self._transfer_generation
        self.transferChanged.emit()
        future = self._transfer_executor.submit(
            prepare_lending_action, self._lending_preflight_service,
            self._lending_action_profiles, active, request,
        )
        future.add_done_callback(
            lambda completed, current=generation: self._transfer_finished(current, completed),
        )

    def cancelExternalTransfer(self, request: dict[str, object]) -> dict[str, object]:
        context = self._external_transfer
        action = self._transfer_flow.current
        if (
            context is None
            or action is None
            or request.get("flow_id") != context.get("flow_id")
            or request.get("action_id") != action.action_id
            or request.get("prepared_digest") != action.digest
        ):
            return self._external_refusal(request, "ACTION_MISMATCH")
        self._external_transfer = None
        self._external_completion = None
        self._cancel_transfer_request(clear_recipient=True)
        self._guard_open_notice = "Transfer cancelled by Hermes"
        self.guardNoticeChanged.emit()
        self._guard_notice_timer.start()
        self._set_screen("main")
        return {
            "authority_version": AUTHORITY_VERSION,
            "kind": "transfer_cancelled" if context.get("kind") == "prepare_transfer" else "action_cancelled",
            "flow_id": request["flow_id"], "action_id": request["action_id"],
            "code": "ACTION_CANCELLED",
        }

    @staticmethod
    def _external_refusal(
        request: dict[str, object], code: str,
    ) -> dict[str, object]:
        return {
            "authority_version": AUTHORITY_VERSION, "kind": "transfer_refused",
            "flow_id": request.get("flow_id"), "action_id": request.get("action_id"),
            "code": code,
        }

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
            normalized = normalize_recipient(recipient, active.address)
            if asset_id == "usdc":
                amount = self.maximumTransferAmount(
                    network_id, asset_id, normalized,
                )
                if not amount:
                    code = self._mainnet_executor.policy.draft_amount_code(
                        network_id, asset_id, 1, normalized,
                    )
                    if code is not None:
                        self._set_transfer_error(_mainnet_policy_error_message(code))
                        return False
                    raise TransferPreflightError(
                        TransferPreflightCode.INSUFFICIENT_USDC,
                    )
                self.transferMaximumReady.emit(
                    network_id, asset_id, normalized, amount,
                )
                return True
            code = self._mainnet_executor.policy.draft_amount_code(
                network_id, asset_id, 1, normalized,
            )
            if code is not None:
                self._set_transfer_error(_mainnet_policy_error_message(code))
                return False
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
                normalized,
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
        self._notify_external_transfer("REJECTED", "LOCAL_CANCELLED")
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._set_screen("main")

    @Slot()
    def editTransfer(self) -> None:
        if self._mainnet_in_progress:
            return
        action = self._transfer_flow.current
        if action is not None and action.action_type == "lending":
            self._notify_external_transfer("REJECTED", "ACTION_EDIT_REQUESTED")
            self._clear_mainnet_result()
            self._cancel_transfer_request(clear_recipient=True)
            self._guard_open_notice = "Aave action cancelled · change it in Hermes"
            self.guardNoticeChanged.emit()
            self._guard_notice_timer.start()
            self._set_screen("main")
            return
        self._notify_external_transfer("REJECTED", "TRANSFER_EDITED")
        self._clear_mainnet_result()
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

    @Slot(str, result=bool)
    def submitMainnetExecution(self, password: str) -> bool:
        action = self._transfer_flow.current
        active = self._state.active_profile
        if (
            len(password) < MIN_PASSWORD_LENGTH
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
        self._public_data_updated_text = (
            "Cached · updating…"
            if self._cached_network_ids or self._price_cached
            else "Refreshing…"
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
            if self._flow == "create":
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
            approval_open = self._current_screen == "approvals" or self._current_screen.startswith("revoke_")
            invalidated = self._transfer_flow.profile_changed(profile_id)
            recovery_invalidated = self._recovery_flow.profile_changed(profile_id)
            revoke_invalidated = self._revoke_flow.profile_changed(profile_id)
            if recovery_invalidated or recovery_open:
                self._cancel_recovery_action(clear_clipboard=True)
            if invalidated:
                self._notify_external_transfer("FAILED", "ACCOUNT_CHANGED")
            if revoke_invalidated or approval_open:
                self._cancel_revoke_action(clear_snapshots=True)
            self.activeProfileChanged.emit()
            self._load_public_cache()
            self.historyChanged.emit()
            if (
                invalidated or recovery_invalidated or recovery_open
                or revoke_invalidated or approval_open
            ):
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
            if self._current_screen == "approvals" or self._current_screen.startswith("revoke_"):
                self._cancel_revoke_action(clear_snapshots=False)
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
    def showTrustedRecipients(self) -> None:
        if (
            not self._state.profiles
            or self._closed
            or self._mainnet_in_progress
            or self._transfer_flow.current is not None
            or self._transfer_flow.pending is not None
            or self._recovery_flow.current is not None
            or self._revoke_flow.current is not None
            or self._revoke_flow.pending is not None
        ):
            return
        self._set_error("")
        self._trusted_route_key = None
        self._trusted_recipient_address = None
        self._trusted_review_digest = ""
        self._trusted_apply_review_digest = ""
        try:
            loaded = self._trusted_store.load()
            self._trusted_saved = loaded
            self._trusted_working = loaded
            self._trusted_available = True
            self._trusted_status = "Draft only · transfers remain disabled"
        except TrustedDraftUnavailable:
            self._trusted_saved = TrustedPolicyDraft()
            self._trusted_working = TrustedPolicyDraft()
            self._trusted_available = False
            self._trusted_status = "Trusted recipients draft is unavailable"
        self._refresh_trusted_policy_status()
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_recipients")

    @Slot()
    def closeTrustedRecipients(self) -> None:
        self._trusted_working = self._trusted_saved
        self._trusted_route_key = None
        self._trusted_recipient_address = None
        self._trusted_review_digest = ""
        self._trusted_apply_review_digest = ""
        self._settings_section = "security"
        self.settingsSectionChanged.emit()
        self.trustedDraftChanged.emit()
        self._set_screen("settings_info")

    def _critical_flow_active(self) -> bool:
        return (
            self._transfer_preparing
            or self._mainnet_in_progress
            or self._transfer_flow.pending is not None
            or self._transfer_flow.current is not None
            or self._revoke_flow.pending is not None
            or self._revoke_flow.current is not None
            or self._recovery_flow.current is not None
            or self._flow != "none"
        )

    def _refresh_trusted_policy_status(self) -> bool:
        policy = self._mainnet_executor.policy
        policy.refresh()
        self._trusted_active_revision = policy.policy_revision
        self._trusted_active_digest = policy.policy_digest_value
        if self._policy_control_client is None:
            return False
        try:
            response = self._policy_control_client.status()
        except (ControlUnavailable, ControlProtocolError, OSError):
            return False
        self._trusted_active_revision = int(response["policy_revision"])
        self._trusted_active_digest = str(response["policy_digest"])
        source = response.get("source_draft_digest")
        self._trusted_active_source_digest = source if isinstance(source, str) else None
        self._trusted_transfer_enabled = bool(response.get("transfer_authority_enabled", False))
        self._trusted_lending_enabled = bool(response.get("lending_authority_enabled", False))
        self._trusted_authority_state = str(response.get("authority_state", "INVALID"))
        return True

    @Slot(str, str, result=bool)
    def saveTrustedLendingLimits(self, amount: str, fee: str) -> bool:
        if not self._trusted_available or self._current_screen != "trusted_recipients":
            return False
        try:
            self._trusted_working = self._trusted_working.with_lending_limits(amount, fee)
        except TrustedDraftError as exc:
            self._set_error(str(exc))
            return False
        self._trusted_review_digest = ""
        self._trusted_status = "Unsaved draft changes"
        self._set_error("")
        self.trustedDraftChanged.emit()
        return True

    @Slot(result=bool)
    def removeTrustedLendingLimits(self) -> bool:
        if not self._trusted_available or self._current_screen != "trusted_recipients":
            return False
        self._trusted_working = self._trusted_working.without_lending_limits()
        self._trusted_review_digest = ""
        self._trusted_status = "Unsaved draft changes"
        self.trustedDraftChanged.emit()
        return True

    @Slot()
    def beginTrustedRoute(self) -> None:
        if not self._trusted_available or self._current_screen != "trusted_recipients":
            return
        self._trusted_route_key = None
        self._trusted_recipient_address = None
        self._set_error("")
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_route")

    @Slot(str, str, result=bool)
    def editTrustedRoute(self, network: str, asset: str) -> bool:
        if (
            not self._trusted_available
            or self._current_screen != "trusted_recipients"
            or self._trusted_working.route(network, asset) is None
        ):
            return False
        self._trusted_route_key = network, asset
        self._trusted_recipient_address = None
        self._set_error("")
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_route")
        return True

    @Slot(str, str, str, str, result=bool)
    def saveTrustedRoute(
        self, network: str, asset: str, amount_input: str, fee_input: str,
    ) -> bool:
        self._set_error("")
        if (
            not self._trusted_available
            or self._current_screen != "trusted_route"
            or (network, asset) not in ROUTE_ORDER
        ):
            self._set_error("Select a supported network and asset")
            return False
        original = (
            self._trusted_working.route(*self._trusted_route_key)
            if self._trusted_route_key is not None else None
        )
        if (
            original is None
            and self._trusted_working.route(network, asset) is not None
        ):
            self._set_error("This transfer route already exists")
            return False
        if original is not None and original.key != (network, asset):
            self._set_error("Create a new route to change network or asset")
            return False
        try:
            route = TrustedRouteDraft(
                network,
                asset,
                NETWORK_BY_ID[network].chain_id,
                parse_cap(amount_input, asset),
                parse_fee_cap(fee_input),
                original.recipients if original is not None else (),
            )
            working = self._trusted_working
            self._trusted_working = working.with_route(route)
        except (TrustedDraftError, ValueError) as exc:
            self._set_error(str(exc))
            return False
        self._trusted_route_key = route.key
        self._trusted_review_digest = ""
        self._trusted_status = "Unsaved draft changes"
        self.trustedDraftChanged.emit()
        return True

    @Slot()
    def closeTrustedRoute(self) -> None:
        self._trusted_route_key = None
        self._trusted_recipient_address = None
        self._set_error("")
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_recipients")

    @Slot(result=bool)
    def deleteTrustedRoute(self) -> bool:
        if self._current_screen != "trusted_route" or self._trusted_route_key is None:
            return False
        self._trusted_working = self._trusted_working.without_route(
            *self._trusted_route_key,
        )
        self._trusted_route_key = None
        self._trusted_recipient_address = None
        self._trusted_review_digest = ""
        self._trusted_status = "Unsaved draft changes"
        self._set_error("")
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_recipients")
        return True

    @Slot(str, result=bool)
    def beginTrustedRecipient(self, address: str = "") -> bool:
        if self._current_screen != "trusted_route" or self._trusted_route_key is None:
            return False
        route = self._trusted_working.route(*self._trusted_route_key)
        if route is None:
            return False
        if address and not any(
            item.address.lower() == address.lower() for item in route.recipients
        ):
            return False
        self._trusted_recipient_address = address or None
        self._set_error("")
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_recipient")
        return True

    @Slot(str, str, str, result=bool)
    def saveTrustedRecipient(
        self, label: str, address: str, amount_input: str,
    ) -> bool:
        self._set_error("")
        active = self._state.active_profile
        route = (
            self._trusted_working.route(*self._trusted_route_key)
            if self._trusted_route_key is not None else None
        )
        if not self._trusted_available or active is None or route is None:
            return False
        if self._current_screen != "trusted_recipient":
            return False
        try:
            normalized = validate_draft_address(address, active.address)
            recipient = TrustedRecipientDraft(
                validate_label(label), normalized, parse_cap(amount_input, route.asset),
            )
            if int(recipient.max_amount_atomic) > int(route.max_amount_atomic):
                raise TrustedDraftError("Recipient limit exceeds route limit")
            retained = tuple(
                item for item in route.recipients
                if self._trusted_recipient_address is None
                or item.address.lower() != self._trusted_recipient_address.lower()
            )
            if any(item.address.lower() == normalized.lower() for item in retained):
                raise TrustedDraftError("Recipient already exists on this route")
            changed = replace(route, recipients=retained + (recipient,))
            self._trusted_working = self._trusted_working.with_route(changed)
        except (TrustedDraftError, ValueError) as exc:
            self._set_error(str(exc))
            return False
        self._trusted_recipient_address = normalized
        self._trusted_review_digest = ""
        self._trusted_status = "Unsaved draft changes"
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_route")
        return True

    @Slot()
    def closeTrustedRecipient(self) -> None:
        self._trusted_recipient_address = None
        self._set_error("")
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_route")

    @Slot(result=bool)
    def deleteTrustedRecipient(self) -> bool:
        route = (
            self._trusted_working.route(*self._trusted_route_key)
            if self._trusted_route_key is not None else None
        )
        if route is None or self._trusted_recipient_address is None:
            return False
        if self._current_screen != "trusted_recipient":
            return False
        changed = replace(route, recipients=tuple(
            item for item in route.recipients
            if item.address.lower() != self._trusted_recipient_address.lower()
        ))
        self._trusted_working = self._trusted_working.with_route(changed)
        self._trusted_recipient_address = None
        self._trusted_review_digest = ""
        self._trusted_status = "Unsaved draft changes"
        self._set_error("")
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_route")
        return True

    @Slot(result=bool)
    def showTrustedDraftReview(self) -> bool:
        if not self._trusted_available or self._current_screen != "trusted_recipients":
            return False
        try:
            envelope = self._trusted_working.to_envelope()
        except TrustedDraftError as exc:
            self._set_error(str(exc))
            return False
        self._set_error("")
        self._trusted_review_digest = _trusted_envelope_digest(envelope)
        self._set_screen("trusted_review")
        return True

    @Slot()
    def closeTrustedDraftReview(self) -> None:
        self._trusted_review_digest = ""
        self._set_error("")
        self._set_screen("trusted_recipients")

    @Slot()
    def beginTrustedDraftPassword(self) -> None:
        if (
            self._trusted_available
            and self._current_screen == "trusted_review"
            and self._trusted_review_digest
        ):
            self._set_error("")
            self._set_screen("trusted_password")

    @Slot()
    def closeTrustedDraftPassword(self) -> None:
        self._set_error("")
        self._set_screen("trusted_review")

    @Slot(str, result=bool)
    def submitTrustedDraft(self, password: str) -> bool:
        self._set_error("")
        if (
            len(password) < MIN_PASSWORD_LENGTH
            or not self._trusted_available
            or self._current_screen != "trusted_password"
            or not self._trusted_review_digest
        ):
            self._set_error("Enter the Wallet password")
            return False
        try:
            candidate = self._trusted_working.canonical()
            candidate_envelope = candidate.to_envelope()
            if not hmac.compare_digest(
                _trusted_envelope_digest(candidate_envelope),
                self._trusted_review_digest,
            ):
                self._trusted_review_digest = ""
                self._set_error("Draft changed; review it again")
                self._set_screen("trusted_recipients")
                return False
            self._repository.authenticate(password)
            del password
            self._trusted_store.save(candidate)
            self._trusted_saved = candidate
            self._trusted_working = candidate
            self._trusted_status = (
                "Draft saved. Transfers remain disabled until policy activation."
            )
            self._trusted_review_digest = ""
            self.trustedDraftChanged.emit()
            self._set_screen("trusted_recipients")
            return True
        except AuthenticationFailedError:
            self._set_error("Authentication failed")
        except TrustedDraftError as exc:
            self._set_error(str(exc))
        except TrustedDraftUnavailable:
            self._set_error("Trusted recipients draft could not be saved")
        except VaultUnavailableError:
            self._set_error("Wallet vault is unavailable")
        finally:
            try:
                del password
            except UnboundLocalError:
                pass
        return False

    @Slot(result=bool)
    def showTrustedApplyReview(self) -> bool:
        self._set_error("")
        if (
            not self.trustedCanApply
            or self._current_screen != "trusted_recipients"
            or not self._refresh_trusted_policy_status()
        ):
            self._set_error("Guard is unavailable. Draft remains saved.")
            return False
        try:
            saved = self._trusted_store.load()
            envelope = saved.to_envelope()
        except (TrustedDraftUnavailable, TrustedDraftError):
            self._set_error("Trusted recipients draft is unavailable")
            return False
        self._trusted_saved = saved
        self._trusted_working = saved
        self._trusted_apply_review_digest = trusted_draft_digest(envelope)
        self._trusted_apply_policy_digest = str(envelope["policy_digest"])
        self._trusted_policy_operation = "apply"
        self._trusted_apply_expected_revision = self._trusted_active_revision
        self._trusted_apply_expected_digest = self._trusted_active_digest
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_apply_review")
        return True

    @Slot(result=bool)
    def showTrustedInitializationReview(self) -> bool:
        self._set_error("")
        if (
            self._current_screen != "trusted_recipients"
            or not self._refresh_trusted_policy_status()
            or not self.trustedCanInitializeAuthority
        ):
            self._set_error("Authority initialization is unavailable")
            return False
        self._trusted_policy_operation = "initialize"
        self._trusted_apply_expected_revision = self._trusted_active_revision
        self._trusted_apply_expected_digest = self._trusted_active_digest
        self._trusted_apply_review_digest = self._trusted_active_digest
        self._trusted_apply_policy_digest = self._trusted_active_digest
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_apply_review")
        return True

    @Slot(bool, result=bool)
    def showTrustedCapabilityReview(self, enabled: bool) -> bool:
        allowed = self.trustedCanActivateLending if enabled else self.trustedCanDeactivateLending
        if not allowed or not self._refresh_trusted_policy_status():
            self._set_error("Lending authority change is unavailable")
            return False
        try:
            saved = self._trusted_store.load()
            envelope = saved.to_envelope()
            disabled = saved.to_policy()
            candidate = Policy(
                "3", "2", False, disabled.transfer_rules, enabled,
                disabled.lending_rules,
            )
        except (TrustedDraftUnavailable, TrustedDraftError, ValueError):
            self._set_error("Authority policy draft is unavailable")
            return False
        self._trusted_saved = saved
        self._trusted_working = saved
        self._trusted_apply_review_digest = trusted_draft_digest(envelope)
        self._trusted_apply_policy_digest = policy_digest(candidate.to_dict())
        self._trusted_apply_expected_revision = self._trusted_active_revision
        self._trusted_apply_expected_digest = self._trusted_active_digest
        self._trusted_policy_operation = "activate" if enabled else "deactivate"
        self.trustedDraftChanged.emit()
        self._set_screen("trusted_apply_review")
        return True

    @Slot()
    def closeTrustedApplyReview(self) -> None:
        self._clear_trusted_apply_review()
        self._set_error("")
        self._set_screen("trusted_recipients")

    @Slot()
    def beginTrustedApplyPassword(self) -> None:
        if (
            self._current_screen == "trusted_apply_review"
            and self._trusted_apply_review_digest
            and not self._critical_flow_active()
        ):
            self._set_error("")
            self._set_screen("trusted_apply_password")

    @Slot()
    def closeTrustedApplyPassword(self) -> None:
        self._set_error("")
        self._set_screen("trusted_apply_review")

    @Slot(str, result=bool)
    def submitTrustedApply(self, password: str) -> bool:
        self._set_error("")
        if (
            len(password) < MIN_PASSWORD_LENGTH
            or self._current_screen != "trusted_apply_password"
            or not self._trusted_apply_review_digest
            or self._policy_control_client is None
            or self._critical_flow_active()
        ):
            self._set_error("Policy application is unavailable")
            return False
        try:
            if self._trusted_policy_operation == "initialize":
                if (
                    self._trusted_apply_expected_revision != 0
                    or not hmac.compare_digest(
                        self._trusted_apply_expected_digest,
                        self._trusted_apply_review_digest,
                    )
                ):
                    raise ControlProtocolError("Initialization review changed")
                self._repository.authenticate(password)
                del password
                response = self._policy_control_client.initialize_authority_state(
                    self._trusted_apply_expected_revision,
                    self._trusted_apply_expected_digest,
                )
                if response.get("kind") != "authority_initialized":
                    self._clear_trusted_apply_review()
                    self._set_error(_policy_apply_error(str(response.get("code", ""))))
                    self._set_screen("trusted_recipients")
                    return False
                self._trusted_authority_state = str(response["authority_state"])
                self._trusted_status = (
                    "Authority state initialized. Send and Lending remain disabled."
                )
                self._clear_trusted_apply_review()
                self.trustedDraftChanged.emit()
                self._set_screen("trusted_recipients")
                return True
            saved = self._trusted_store.load()
            envelope = saved.to_envelope()
            disabled = saved.to_policy()
            candidate = (
                disabled if self._trusted_policy_operation == "apply"
                else Policy(
                    "3", "2", False, disabled.transfer_rules,
                    self._trusted_policy_operation == "activate", disabled.lending_rules,
                )
            )
            if (
                not hmac.compare_digest(
                    trusted_draft_digest(envelope), self._trusted_apply_review_digest,
                )
                or not hmac.compare_digest(
                    policy_digest(candidate.to_dict()), self._trusted_apply_policy_digest,
                )
            ):
                self._clear_trusted_apply_review()
                self._set_error("Draft changed; review it again")
                self._set_screen("trusted_recipients")
                return False
            self._repository.authenticate(password)
            del password
            if self._trusted_policy_operation == "apply":
                response = self._policy_control_client.apply(
                    self._trusted_apply_expected_revision,
                    self._trusted_apply_expected_digest,
                    self._trusted_apply_review_digest,
                    self._trusted_apply_policy_digest,
                )
                expected_kind = "policy_applied"
            else:
                response = self._policy_control_client.set_capability(
                    self._trusted_policy_operation == "activate",
                    self._trusted_apply_expected_revision,
                    self._trusted_apply_expected_digest,
                    self._trusted_apply_review_digest,
                    self._trusted_apply_policy_digest,
                )
                expected_kind = (
                    "policy_activated" if self._trusted_policy_operation == "activate"
                    else "policy_deactivated"
                )
            if response.get("kind") != expected_kind:
                self._clear_trusted_apply_review()
                self._set_error(_policy_apply_error(str(response.get("code", ""))))
                self._set_screen("trusted_recipients")
                return False
            self._trusted_active_revision = int(response["policy_revision"])
            self._trusted_active_digest = str(response["policy_digest"])
            source = response.get("source_draft_digest")
            self._trusted_active_source_digest = source if isinstance(source, str) else None
            self._trusted_transfer_enabled = bool(response.get("transfer_authority_enabled", False))
            self._trusted_lending_enabled = bool(response.get("lending_authority_enabled", False))
            self._trusted_authority_state = str(response.get("authority_state", "INVALID"))
            self._mainnet_executor.policy.refresh()
            self._transfer_flow.close()
            code = str(response.get("code", ""))
            if expected_kind == "policy_activated":
                self._trusted_status = (
                    f"Lending authority activated as revision {self._trusted_active_revision}. "
                    "Send remains disabled."
                )
            elif expected_kind == "policy_deactivated":
                self._trusted_status = (
                    f"Lending authority disabled as revision {self._trusted_active_revision}."
                )
            else:
                self._trusted_status = (
                f"Draft applied as revision {self._trusted_active_revision}. "
                "Send and Lending remain disabled until capability activation."
                if code == "POLICY_REVISION_APPLIED"
                else f"Draft is already active as revision {self._trusted_active_revision}. "
                "Send and Lending remain disabled until capability activation."
                )
            self._clear_trusted_apply_review()
            self.trustedDraftChanged.emit()
            self._set_screen("trusted_recipients")
            return True
        except AuthenticationFailedError:
            self._set_error("Authentication failed")
        except (TrustedDraftUnavailable, TrustedDraftError):
            self._set_error("Trusted recipients draft is unavailable")
        except (ControlUnavailable, ControlProtocolError, OSError):
            self._set_error("Guard is unavailable. Draft remains saved.")
        except VaultUnavailableError:
            self._set_error("Wallet vault is unavailable")
        finally:
            try:
                del password
            except UnboundLocalError:
                pass
        return False

    def _clear_trusted_apply_review(self) -> None:
        self._trusted_apply_review_digest = ""
        self._trusted_apply_policy_digest = ""
        self._trusted_apply_expected_revision = 0
        self._trusted_apply_expected_digest = ""
        self._trusted_policy_operation = ""

    @Slot()
    def showApprovals(self) -> None:
        if (
            not self._state.profiles
            or self._closed
            or self._mainnet_in_progress
            or self._transfer_flow.current is not None
            or self._transfer_flow.pending is not None
            or self._recovery_flow.current is not None
            or self._current_screen == "recovery_reveal"
        ):
            return
        self._clear_mainnet_result()
        self._cancel_revoke_action(clear_snapshots=False)
        self._set_screen("approvals")
        self.refreshApprovals()

    @Slot(result=bool)
    def refreshApprovals(self) -> bool:
        active = self._state.active_profile
        if (
            active is None
            or self._closed
            or self._approval_refreshing
            or self._approval_preparing
            or self._revoke_flow.current is not None
        ):
            return False
        self._approval_generation += 1
        generation = self._approval_generation
        self._approval_refreshing = True
        self._approval_error = ""
        self.approvalChanged.emit()
        future = self._public_data_executor.submit(
            self._allowance_service.inspect_all, active,
        )
        future.add_done_callback(
            lambda completed, current=generation: self._allowances_finished(
                current, completed,
            ),
        )
        return True

    @Slot(str, result=bool)
    def prepareRevoke(self, network_id: str) -> bool:
        active = self._state.active_profile
        snapshot = next(
            (item for item in self._allowance_snapshots if item.network_id == network_id),
            None,
        )
        if (
            active is None
            or snapshot is None
            or self._closed
            or self._approval_refreshing
            or self._approval_preparing
            or self._mainnet_in_progress
            or self._current_screen != "approvals"
        ):
            return False
        mapped = allowance_snapshot_to_map(
            snapshot, self._revoke_policy,
        )
        if not mapped["revokeAvailable"]:
            return False
        try:
            request = self._revoke_flow.begin(active.profile_id, network_id)
        except (RevokeFlowError, RevokePreflightError):
            return False
        self._approval_preparing = True
        self._approval_error = ""
        self._approval_generation += 1
        generation = self._approval_generation
        self.approvalChanged.emit()
        future = self._transfer_executor.submit(
            self._revoke_preflight_service.prepare, request, active,
        )
        future.add_done_callback(
            lambda completed, current=generation: self._revoke_finished(
                current, completed,
            ),
        )
        return True

    @Slot()
    def editRevoke(self) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        self._cancel_revoke_action(clear_snapshots=False)
        self._set_screen("approvals")
        self.refreshApprovals()

    @Slot(result=bool)
    def beginRevokeExecution(self) -> bool:
        action = self._revoke_flow.current
        if (
            action is None
            or self._closed
            or self._revoke_flow.state is not RevokeFlowState.PREPARED
            or self._revoke_policy.evaluate(action) is not None
        ):
            return False
        if self._revoke_flow.is_expired():
            self._show_revoke_failure(action, MainnetTransferCode.ACTION_EXPIRED)
            return False
        self._clear_mainnet_result()
        self._set_screen("revoke_confirm")
        return True

    @Slot(str, result=bool)
    def submitRevoke(self, password: str) -> bool:
        action = self._revoke_flow.current
        active = self._state.active_profile
        if (
            len(password) < MIN_PASSWORD_LENGTH
            or action is None
            or active is None
            or self._closed
            or self._current_screen != "revoke_confirm"
            or self._mainnet_in_progress
        ):
            return False
        digest = self._revoke_flow.accepted_digest
        permit = self._revoke_flow.begin_execution(
            action.action_id, digest, active.profile_id,
        )
        if permit is None:
            self._show_revoke_failure(action, MainnetTransferCode.ACTION_INVALID)
            return False
        self._mainnet_in_progress = True
        self._mainnet_result = None
        self._revoke_expiry_timer.stop()
        self._approval_generation += 1
        generation = self._approval_generation
        self.approvalChanged.emit()
        self.transferChanged.emit()
        self._set_screen("revoke_submit")
        future = self._transfer_executor.submit(
            self._mainnet_executor.execute,
            action,
            digest,
            password,
            permit,
        )
        del password
        future.add_done_callback(
            lambda completed, current=generation: self._revoke_execution_finished(
                current, completed,
            ),
        )
        return True

    @Slot()
    def cancelRevoke(self) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        self._cancel_revoke_action(clear_snapshots=False)
        self._set_screen("approvals")
        self.refreshApprovals()

    @Slot()
    def finishRevoke(self) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        self._cancel_revoke_action(clear_snapshots=False)
        self._set_screen("approvals")
        self.refreshApprovals()

    @Slot()
    def closeApprovals(self) -> None:
        if self._mainnet_in_progress:
            return
        self._clear_mainnet_result()
        self._cancel_revoke_action(clear_snapshots=False)
        self._settings_section = "security"
        self.settingsSectionChanged.emit()
        self._set_screen("settings_info")

    @Slot()
    def showRecoveryReview(self) -> None:
        active = self._state.active_profile
        if (
            active is None
            or self._closed
            or self._mainnet_in_progress
            or self._transfer_flow.current is not None
            or self._revoke_flow.current is not None
            or self._revoke_flow.pending is not None
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

    @Slot()
    def showGuardOpenNotice(self) -> None:
        if self._closed:
            return
        self._guard_open_notice = "Opened by Guard · no Guard action authorized"
        self._guard_notice_timer.start()
        self.guardNoticeChanged.emit()

    def attach_recovery_display(self, display: RecoverySecretDisplay) -> None:
        self._recovery_display = display

    @Slot()
    def shutdown(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._public_data_generation += 1
        self._approval_generation += 1
        self._receipt_generation += 1
        self._receipt_cancelled.set()
        self._guard_notice_timer.stop()
        self._clear_guard_notice()
        self._clear_mainnet_result()
        self._cancel_transfer_request(clear_recipient=True)
        self._cancel_revoke_action(clear_snapshots=True)
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
        self._cancel_revoke_action(clear_snapshots=True)
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
            self._flow = "none"
            self.flowChanged.emit()
            self._set_screen("main")
        except VaultUnavailableError:
            self._state = WalletShellState()
            self.profilesChanged.emit()
            self.activeProfileChanged.emit()
            self._flow = "none"
            self.flowChanged.emit()
            self._set_screen("unavailable")

    def _trusted_route_map(
        self, route: TrustedRouteDraft,
    ) -> dict[str, object]:
        spec = transfer_route(route.network, route.asset)
        return {
            "networkId": route.network,
            "networkLabel": NETWORK_BY_ID[route.network].label,
            "assetId": route.asset,
            "assetLabel": spec.symbol,
            "chainId": str(route.chain_id),
            "routeAmount": route.display_amount(),
            "routeAmountUsd": estimate_asset_usd(
                int(route.max_amount_atomic), spec.decimals, route.asset,
                self._price_snapshot,
            ),
            "feeAmount": route.display_fee(),
            "feeUsd": estimate_wei_usd(
                int(route.max_total_fee_wei), self._price_snapshot,
            ),
            "recipientCount": len(route.recipients),
            "recipients": [
                self._trusted_recipient_map(route, item)
                for item in route.recipients
            ],
        }

    def _trusted_recipient_map(
        self,
        route: TrustedRouteDraft,
        recipient: TrustedRecipientDraft,
    ) -> dict[str, object]:
        spec = transfer_route(route.network, route.asset)
        return {
            "label": recipient.label,
            "address": recipient.address,
            "shortAddress": f"{recipient.address[:8]}…{recipient.address[-6:]}",
            "maxAmount": recipient.display_amount(route.asset),
            "maxAmountUsd": estimate_asset_usd(
                int(recipient.max_amount_atomic), spec.decimals, route.asset,
                self._price_snapshot,
            ),
        }

    def _replace_profiles(
        self, profiles: tuple[ProfileSummary, ...], active_id: str | None = None,
    ) -> None:
        valid_ids = {profile.profile_id for profile in profiles}
        selected = active_id or self._settings.load_active_id(valid_ids)
        self._state.replace_profiles(profiles, selected)
        self.profilesChanged.emit()
        self.activeProfileChanged.emit()
        self._load_public_cache()
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
        self._cancel_revoke_action(clear_snapshots=True)
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
        except LendingPreflightError as error:
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

    def _allowances_finished(
        self, generation: int, future: Future[tuple[AllowanceSnapshot, ...]],
    ) -> None:
        if self._closed:
            return
        try:
            result: object = future.result()
        except Exception:
            result = None
        self._approvalReady.emit(generation, result)

    def _revoke_finished(
        self, generation: int, future: Future[PreparedRevokeAction],
    ) -> None:
        if self._closed:
            return
        try:
            result: object = future.result()
        except RevokePreflightError as error:
            result = error
        except Exception:
            result = RevokePreflightError(RevokePreflightCode.RPC_UNAVAILABLE)
        self._revokeReady.emit(generation, result)

    def _revoke_execution_finished(
        self, generation: int, future: Future[MainnetTransferResult],
    ) -> None:
        if self._closed:
            return
        try:
            result: object = future.result()
        except Exception:
            result = None
        self._revokeExecutionReady.emit(generation, result)

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
        if (
            result.broadcast_attempted
            and result.history_status in {
                HistoryStatus.PENDING, HistoryStatus.CONFIRMED, HistoryStatus.UNKNOWN,
            }
        ):
            self._notify_external_transfer(
                "COMPLETED", result.code.value, result.history_status.value,
            )
        else:
            self._notify_external_transfer("FAILED", result.code.value)
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
        self.approvalChanged.emit()

    @Slot(int, object)
    def _accept_allowances(self, generation: int, result: object) -> None:
        if generation != self._approval_generation or self._closed:
            return
        self._approval_refreshing = False
        if (
            not isinstance(result, tuple)
            or len(result) != 2
            or any(not isinstance(item, AllowanceSnapshot) for item in result)
            or self._state.active_profile is None
            or any(item.owner != self._state.active_profile.address for item in result)
        ):
            self._allowance_snapshots = ()
            self._approval_error = "Approval data is unavailable"
        else:
            self._allowance_snapshots = result
            self._approval_error = ""
        self.approvalChanged.emit()

    @Slot(int, object)
    def _accept_revoke_preflight(self, generation: int, result: object) -> None:
        if generation != self._approval_generation or self._closed:
            return
        self._approval_preparing = False
        active = self._state.active_profile
        if isinstance(result, RevokePreflightError):
            self._revoke_flow.close()
            self._approval_error = _revoke_error_message(result.code)
            self.approvalChanged.emit()
            return
        if (
            not isinstance(result, PreparedRevokeAction)
            or active is None
            or active.profile_id != result.profile_id
            or active.address != result.sender
            or not self._revoke_flow.still_pending(result.action_id, result.profile_id)
            or not self._revoke_flow.accept(result)
        ):
            self._revoke_flow.close()
            self._approval_error = "Revoke preparation expired"
            self.approvalChanged.emit()
            return
        try:
            records = self._history_store.append(_revoke_history_record(result))
        except (HistoryUnavailableError, HistoryValidationError, StorageError):
            self._revoke_flow.close()
            self._history_available = False
            self.historyChanged.emit()
            self._approval_error = "History unavailable · revoke was not prepared"
            self.approvalChanged.emit()
            return
        self._history_records = records
        self._history_available = True
        self._approval_error = ""
        self.historyChanged.emit()
        remaining_ms = max(
            1,
            int((result.expires_at - datetime.now(UTC)).total_seconds() * 1000) + 1,
        )
        self._revoke_expiry_timer.start(remaining_ms)
        self.approvalChanged.emit()
        self._set_screen("revoke_review")

    @Slot(int, object)
    def _accept_revoke_result(self, generation: int, result: object) -> None:
        if generation != self._approval_generation or self._closed:
            return
        action = self._revoke_flow.current
        self._mainnet_in_progress = False
        if (
            not isinstance(result, MainnetTransferResult)
            or action is None
            or result.action_id != action.action_id
            or result.action_type != "revoke"
            or not self._revoke_flow.complete_execution(action.action_id)
        ):
            if action is None:
                return
            result = self._safe_mainnet_result(
                action, MainnetTransferCode.SIGNING_FAILED,
            )
            self._revoke_flow.close()
        self._revoke_expiry_timer.stop()
        self._mainnet_result = result
        self._load_history()
        self.approvalChanged.emit()
        self.transferChanged.emit()
        self._set_screen("revoke_result")
        if (
            result.transaction_hash
            and result.history_status in {HistoryStatus.PENDING, HistoryStatus.UNKNOWN}
        ):
            self._start_receipt_check(result.action_id, track=True)

    @Slot(int, object)
    def _accept_transfer_preflight(self, generation: int, result: object) -> None:
        if generation != self._transfer_generation or self._closed:
            return
        self._transfer_preparing = False
        active = self._state.active_profile
        if isinstance(result, (TransferPreflightError, LendingPreflightError)):
            self._transfer_flow.close()
            message = (
                _transfer_error_message(result.code)
                if isinstance(result, TransferPreflightError)
                else "Aave action preflight failed"
            )
            self._set_transfer_error(message)
            self.transferChanged.emit()
            code = result.code.value if isinstance(result, TransferPreflightError) else result.code
            self._finish_external_preflight(None, code)
            self._set_screen("send")
            return
        if not isinstance(result, PreparedTransferAction) or active is None:
            self._transfer_flow.close()
            self._set_transfer_error("Transaction preparation failed")
            self.transferChanged.emit()
            self._finish_external_preflight(None, "TRANSFER_PREPARATION_FAILED")
            self._set_screen("send")
            return
        policy_code = self._mainnet_executor.policy.evaluate(result)
        if result.action_type == "lending" and policy_code is not None:
            self._transfer_flow.close()
            self._set_transfer_error("Aave action exceeds the active policy")
            self.transferChanged.emit()
            self._finish_external_preflight(None, policy_code.value)
            self._set_screen("send")
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
            self._finish_external_preflight(None, "ACTION_EXPIRED")
            self._set_screen("send")
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
            self._finish_external_preflight(None, "HISTORY_UNAVAILABLE")
            self._set_screen("send")
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
        self._finish_external_preflight(result, "TRANSFER_PREPARED")

    def _finish_external_preflight(
        self, action: PreparedTransferAction | None, code: str,
    ) -> None:
        completion = self._external_completion
        context = self._external_transfer
        if completion is None or context is None:
            return
        self._external_completion = None
        if action is None:
            self._external_transfer = None
            completion(self._external_refusal(context, code))
            return
        self._external_transfer["prepared_digest"] = action.digest
        if action.action_type == "lending":
            completion({
                "authority_version": AUTHORITY_VERSION, "kind": "lending_action_prepared",
                "flow_id": context["flow_id"], "action_id": action.action_id,
                "profile_id": action.profile_id, "sender": action.sender,
                "requested_action": context["action"],
                "amount_mode": action.amount_mode, "next_action": action.method,
                "network": "base", "asset": "usdc",
                "amount_atomic": str(action.amount_atomic),
                "target": action.transaction.to, "method": action.method,
                "max_total_fee_wei": str(action.max_total_fee_wei),
                "l2_fee_ceiling_wei": str(action.l2_fee_ceiling_wei),
                "l1_fee_upper_bound_wei": str(action.l1_fee_upper_bound_wei),
                "prepared_digest": action.digest,
                "created_at": context["created_at"], "expires_at": context["expires_at"],
                "policy_revision": action.policy_revision,
                "policy_digest": action.policy_digest,
                "action_profile_digest": action.action_profile_digest,
                "code": "LENDING_ACTION_PREPARED",
            })
            return
        completion({
            "authority_version": AUTHORITY_VERSION, "kind": "transfer_prepared",
            "flow_id": context["flow_id"], "action_id": action.action_id,
            "profile_id": action.profile_id, "sender": action.sender,
            "recipient": action.recipient, "network": action.network_id,
            "asset": action.asset_id, "amount_atomic": str(action.amount_atomic),
            "max_total_fee_wei": str(action.max_total_fee_wei),
            "prepared_digest": action.digest,
            "created_at": context["created_at"], "expires_at": context["expires_at"],
            "policy_revision": action.policy_revision,
            "policy_digest": action.policy_digest,
            "code": code,
        })

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
                network_id, asset_id, result, recipient,
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

    def _cancel_revoke_action(self, clear_snapshots: bool) -> None:
        changed = (
            self._approval_refreshing
            or self._approval_preparing
            or self._revoke_flow.pending is not None
            or self._revoke_flow.current is not None
            or bool(self._approval_error)
            or (clear_snapshots and bool(self._allowance_snapshots))
        )
        self._approval_generation += 1
        self._revoke_expiry_timer.stop()
        self._revoke_flow.close()
        self._approval_refreshing = False
        self._approval_preparing = False
        self._approval_error = ""
        if clear_snapshots:
            self._allowance_snapshots = ()
        if changed:
            self.approvalChanged.emit()

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
        self._notify_external_transfer("FAILED", "ACTION_EXPIRED")
        self._transfer_preparing = False
        self._set_transfer_error("Transaction preparation expired")
        self.transferChanged.emit()
        self._set_screen("send")

    def _show_mainnet_failure(
        self, action: PreparedTransferAction, code: MainnetTransferCode,
    ) -> None:
        self._notify_external_transfer("FAILED", code.value)
        self._transfer_generation += 1
        self._transfer_expiry_timer.stop()
        self._transfer_flow.close()
        self._transfer_preparing = False
        self._mainnet_in_progress = False
        self._mainnet_result = self._safe_mainnet_result(action, code)
        self.transferChanged.emit()
        self._set_screen("transfer_result")

    def _notify_external_transfer(
        self, event: str, code: str, outcome: str | None = None,
    ) -> None:
        context = self._external_transfer
        sender = self._guard_status_sender
        if context is None or sender is None or "prepared_digest" not in context:
            return
        update = {
            "status_version": "1",
            "kind": "transfer_status",
            "flow_id": context["flow_id"],
            "action_id": context["action_id"],
            "prepared_digest": context["prepared_digest"],
            "event": event,
            "code": code,
            "outcome": outcome,
        }
        self._external_transfer = None
        self._external_completion = None
        try:
            sender(update)
        except Exception:
            pass

    def _expire_revoke(self) -> None:
        self._revoke_expiry_timer.stop()
        action = self._revoke_flow.current
        if not self._revoke_flow.is_expired():
            action = self._revoke_flow.current
            if action is not None:
                remaining_ms = max(
                    1,
                    int((action.expires_at - datetime.now(UTC)).total_seconds() * 1000) + 1,
                )
                self._revoke_expiry_timer.start(remaining_ms)
            return
        if action is not None and self._current_screen in {
            "revoke_confirm", "revoke_result",
        }:
            self._show_revoke_failure(action, MainnetTransferCode.ACTION_EXPIRED)
            return
        self._approval_preparing = False
        self._approval_error = "Revoke preparation expired"
        self.approvalChanged.emit()
        self._set_screen("approvals")

    def _show_revoke_failure(
        self, action: PreparedRevokeAction, code: MainnetTransferCode,
    ) -> None:
        self._approval_generation += 1
        self._revoke_expiry_timer.stop()
        self._revoke_flow.close()
        self._approval_preparing = False
        self._mainnet_in_progress = False
        self._mainnet_result = self._safe_mainnet_result(action, code)
        self.approvalChanged.emit()
        self.transferChanged.emit()
        self._set_screen("revoke_result")

    @staticmethod
    def _safe_mainnet_result(
        action: PreparedTransferAction | PreparedRevokeAction,
        code: MainnetTransferCode,
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
            (
                "revoke" if isinstance(action, PreparedRevokeAction)
                else "lending_withdraw_all"
                if action.action_type == "lending"
                and action.method == "withdraw" and action.amount_mode == "all"
                else f"lending_{action.method}"
                if action.action_type == "lending" else "transfer"
            ),
        )

    def _clear_mainnet_result(self) -> None:
        if self._mainnet_result is not None:
            self._mainnet_result = None
            self.transferChanged.emit()
            self.approvalChanged.emit()

    def _start_receipt_check(self, action_id: str, track: bool) -> bool:
        if self._closed or self._receipt_checking:
            return False
        self._receipt_cancelled.set()
        self._receipt_cancelled = Event()
        self._receipt_generation += 1
        generation = self._receipt_generation
        self._receipt_checking = True
        self.transferChanged.emit()
        self.approvalChanged.emit()
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
        valid_bundle = (
            snapshot is not None
            and active is not None
            and snapshot.profile_id == active.profile_id
            and snapshot.address == active.address
        )
        live_network_received = False
        if not valid_bundle:
            self._preserve_or_unavailable(requested, "RPC_UNAVAILABLE")
        else:
            assert snapshot is not None
            returned_ids = {item.network_id for item in snapshot.networks}
            if returned_ids != set(requested):
                self._preserve_or_unavailable(requested, "DATA_INVALID")
            else:
                for item in snapshot.networks:
                    if item.status in {PublicDataStatus.LIVE, PublicDataStatus.SIMULATED}:
                        self._network_snapshots[item.network_id] = item
                        self._cached_network_ids.discard(item.network_id)
                        live_network_received |= item.status is PublicDataStatus.LIVE
                    else:
                        self._preserve_or_unavailable(
                            (item.network_id,),
                            item.error_code or "RPC_UNAVAILABLE",
                        )
                if (
                    prices is not None
                    and prices.chain_id == 8453
                    and prices.status is PriceStatus.LIVE
                ):
                    self._price_snapshot = prices
                    self._price_cached = False
                else:
                    self._preserve_price_or_unavailable("DATA_INVALID")
        self._public_data_refreshing = False
        timestamps = [
            item.updated_at for item in self._network_snapshots.values()
            if item.updated_at
        ]
        cached = bool(self._cached_network_ids or self._price_cached)
        self._public_data_updated_text = (
            f"{'Cached · updated' if cached else 'Updated'} {_display_local_time(max(timestamps))}"
            if timestamps else "Refresh unavailable"
        )
        if live_network_received:
            try:
                self._public_cache_store.save(
                    active.profile_id, active.address,
                    self._network_snapshots, self._price_snapshot,
                )
            except StorageError:
                pass
        self.publicDataChanged.emit()
        self.trustedDraftChanged.emit()

    def _load_public_cache(self) -> None:
        self._network_snapshots = {
            spec.network_id: NetworkSnapshot.unavailable(spec, "NOT_REFRESHED")
            for spec in NETWORKS
        }
        self._price_snapshot = PriceSnapshot.unavailable(
            int(datetime.now(UTC).timestamp()), "NOT_REFRESHED",
        )
        self._cached_network_ids.clear()
        self._price_cached = False
        active = self._state.active_profile
        if active is None:
            self._public_data_updated_text = "Not refreshed"
            self.publicDataChanged.emit()
            return
        bundle = self._public_cache_store.load(active.profile_id, active.address)
        if bundle is None:
            self._public_data_updated_text = "Not refreshed"
            self.publicDataChanged.emit()
            return
        for item in bundle.networks:
            self._network_snapshots[item.network_id] = item
            self._cached_network_ids.add(item.network_id)
        self._price_snapshot = bundle.prices
        self._price_cached = True
        timestamps = [item.updated_at for item in bundle.networks if item.updated_at]
        self._public_data_updated_text = (
            f"Cached · updated {_display_local_time(max(timestamps))}"
            if timestamps else "Cached public data"
        )
        self.publicDataChanged.emit()

    def _preserve_or_unavailable(
        self, network_ids: tuple[str, ...], code: str,
    ) -> None:
        for network_id in network_ids:
            current = self._network_snapshots[network_id]
            if (
                current.status in {PublicDataStatus.LIVE, PublicDataStatus.SIMULATED}
                and current.eth is not None and current.usdc is not None
            ):
                self._cached_network_ids.add(network_id)
            else:
                self._network_snapshots[network_id] = NetworkSnapshot.unavailable(
                    NETWORK_BY_ID[network_id], code,
                )

    def _preserve_price_or_unavailable(self, code: str) -> None:
        if self._price_snapshot.status is PriceStatus.LIVE:
            self._price_cached = True
        else:
            self._price_snapshot = PriceSnapshot.unavailable(
                int(datetime.now(UTC).timestamp()), code,
            )

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

    def _clear_guard_notice(self) -> None:
        if self._guard_open_notice:
            self._guard_open_notice = ""
            self.guardNoticeChanged.emit()

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


def _trusted_envelope_digest(value: dict[str, object]) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _policy_apply_error(code: str) -> str:
    return {
        "POLICY_FLOW_ACTIVE": "Finish or cancel the current protected action first",
        "POLICY_DRAFT_CHANGED": "Draft changed; review it again",
        "POLICY_DRAFT_UNAVAILABLE": "Trusted recipients draft is unavailable",
        "POLICY_REVISION_STALE": "Active policy changed; review it again",
        "POLICY_REVISION_WRITE_FAILED": "Policy revision could not be saved",
        "POLICY_REVISION_INVALID": "Active policy requires revalidation",
        "AUTHORITY_STATE_NOT_INITIALIZABLE": "Authority state is not safely initializable",
        "AUTHORITY_STATE_BASELINE_REQUIRED": "Baseline revision 0 is required",
        "AUTHORITY_STATE_INITIALIZATION_FAILED": "Authority state initialization failed closed",
    }.get(code, "Policy application was refused")

def _display_local_time(timestamp: str) -> str:
    parsed = datetime.fromisoformat(timestamp.removesuffix("Z") + "+00:00")
    local = parsed.astimezone()
    return QLocale.system().toString(
        QTime(local.hour, local.minute), QLocale.FormatType.ShortFormat,
    )


def _history_record(action: PreparedTransferAction) -> WalletHistoryRecord:
    created_at = _utc_timestamp(action.created_at)
    lending_action_type = (
        "lending_withdraw_all"
        if action.method == "withdraw" and action.amount_mode == "all"
        else f"lending_{action.method}"
    )
    return WalletHistoryRecord(
        action_id=action.action_id,
        profile_id=action.profile_id,
        action_type=(lending_action_type if action.action_type == "lending" else "transfer"),
        network=action.network_id,
        chain_id=action.chain_id,
        sender=action.sender,
        recipient=action.recipient,
        contract=(action.transaction.to if action.action_type == "lending" else action.token_contract),
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


def _revoke_history_record(action: PreparedRevokeAction) -> WalletHistoryRecord:
    created_at = _utc_timestamp(action.created_at)
    return WalletHistoryRecord(
        action_id=action.action_id,
        profile_id=action.profile_id,
        action_type="revoke",
        network=action.network_id,
        chain_id=action.chain_id,
        sender=action.sender,
        recipient=action.spender,
        contract=action.token_contract,
        token="USDC",
        amount_atomic="0",
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


def _mainnet_policy_error_message(code: MainnetTransferCode) -> str:
    return {
        MainnetTransferCode.POLICY_UNAVAILABLE: "Transfer policy is unavailable",
        MainnetTransferCode.POLICY_AUTHORITY_DISABLED:
            "Transfers are disabled by local policy",
        MainnetTransferCode.POLICY_VERSION_MISMATCH:
            "Transfer policy version does not match",
        MainnetTransferCode.NETWORK_NOT_ALLOWED:
            "Network is not allowed by local policy",
        MainnetTransferCode.ASSET_NOT_ALLOWED:
            "Asset is not allowed by local policy",
        MainnetTransferCode.RECIPIENT_NOT_ALLOWED:
            "Recipient is not allowed by local policy",
        MainnetTransferCode.AMOUNT_LIMIT_EXCEEDED:
            "Amount exceeds the local recipient or route limit",
        MainnetTransferCode.FEE_LIMIT_EXCEEDED:
            "Maximum fee exceeds the local mainnet limit",
    }.get(code, "Transfer is not allowed by local policy")


def _revoke_error_message(code: RevokePreflightCode) -> str:
    return {
        RevokePreflightCode.INVALID_ROUTE: "Select Ethereum or Base",
        RevokePreflightCode.POLICY_UNAVAILABLE: "Local revoke policy is unavailable",
        RevokePreflightCode.FEE_LIMIT_EXCEEDED: "Maximum fee exceeds the local revoke limit",
        RevokePreflightCode.NO_ACTIVE_ALLOWANCE: "No active USDC allowance to revoke",
        RevokePreflightCode.WRONG_CHAIN: "Selected network verification failed",
        RevokePreflightCode.TOKEN_METADATA_INVALID: "USDC contract verification failed",
        RevokePreflightCode.INSUFFICIENT_ETH: "Insufficient ETH for the maximum fee",
        RevokePreflightCode.GAS_ESTIMATE_FAILED: "Network fee estimation failed",
        RevokePreflightCode.DATA_INVALID: "Selected network returned invalid approval data",
        RevokePreflightCode.RPC_UNAVAILABLE: "Selected network data is unavailable",
    }[code]
