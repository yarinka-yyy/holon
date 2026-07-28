from __future__ import annotations

import secrets
from dataclasses import replace
from datetime import UTC, datetime, timedelta

from PySide6.QtCore import QLocale, QTime
from holon_policy import (
    Policy, PolicyRevisionStore, RecipientRule, TransferRule, policy_digest,
)
from holon_policy.baseline import BASELINE_POLICY_DIGEST
from holon_policy.baseline import load_baseline_policy
from holon_lending import ACTION_PROFILES_DIGEST

from holon_wallet.broadcast import (
    TRANSFER_EVENT_TOPIC,
    MainnetBroadcastPolicy,
    MainnetTransferCode,
    MainnetTransferExecutor,
)
from holon_wallet.controller import WalletController, _display_local_time
from holon_wallet.history import HistoryStatus, HistoryStore
from holon_wallet.signer import OfflineSigningPolicy
from holon_wallet.storage import StorageError, WalletPaths
from holon_wallet.transfer import (
    TransferFlowState,
    TransferPreflightCode,
    TransferPreflightError,
    format_atomic_amount,
)
from holon_wallet.trusted_recipients import (
    TrustedPolicyDraft,
    TrustedPolicyDraftStore,
    TrustedRecipientDraft,
    TrustedRouteDraft,
)
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic, import_private_key
from holon_guard_ipc.policy_control import ControlUnavailable
from holon_wallet_control.lending_operation import (
    LendingOperation,
    LendingOperationSnapshot,
    LendingOperationStore,
)
from wallet_public_support import (
    DeferredExecutor,
    ImmediateExecutor,
    StubPublicDataService,
    StubLendingPortfolioService,
    StubPriceService,
    StubTransferPreflightService,
    mainnet_services,
    public_snapshot,
)


def test_public_data_timestamp_uses_system_local_time_format() -> None:
    timestamp = "2026-07-22T07:05:00Z"
    local = datetime.fromisoformat("2026-07-22T07:05:00+00:00").astimezone()
    expected = QLocale.system().toString(
        QTime(local.hour, local.minute), QLocale.FormatType.ShortFormat,
    )

    assert _display_local_time(timestamp) == expected
    assert "UTC" not in _display_local_time(timestamp)


def test_bounded_approval_review_edit_and_wrong_password_are_terminal(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    item.showSettings()
    assert item.showSettingsSection("security")
    item.showApprovals()
    assert item.currentScreen == "approvals"
    assert len(item.approvalRecords) == 2
    assert {record["status"] for record in item.approvalRecords} == {"LIVE"}
    assert all(record["revokeAvailable"] for record in item.approvalRecords)

    assert item.prepareRevoke("base")
    assert item.currentScreen == "revoke_review"
    first = item.revokeAction["actionId"]
    assert item.revokeAction["newAllowance"] == "0 USDC"
    assert item.revokeAction["nativeValueWei"] == "0"
    item.editRevoke()
    assert item.currentScreen == "approvals"
    assert item.prepareRevoke("base")
    assert item.revokeAction["actionId"] != first
    assert item.beginRevokeExecution()
    assert item.currentScreen == "revoke_confirm"
    assert item.submitRevoke(secret + "-wrong")
    assert item.currentScreen == "revoke_result"
    assert item.mainnetResult["code"] == "AUTHENTICATION_FAILED"
    assert item.mainnetResult["actionType"] == "revoke"
    assert item._test_mainnet_rpc.send_calls == 0
    assert item._revoke_flow.current is None
    assert item.historyRecords[0]["actionType"] == "revoke"
    assert item.historyRecords[0]["amountLabel"] == "Allowance → 0"


def password() -> str:
    return secrets.token_urlsafe(18)


def raw_private_key() -> str:
    while True:
        candidate = secrets.token_hex(32)
        try:
            import_private_key(candidate)
            return candidate
        except ValueError:
            continue


def controller(tmp_path, policy_control_client=None) -> WalletController:
    repository = VaultRepository(WalletPaths(tmp_path))
    history = HistoryStore(repository.paths)
    mainnet, tracker, rpc = mainnet_services(repository, history)
    item = WalletController(
        repository,
        StubPublicDataService(),
        history,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        mainnet_executor=mainnet,
        receipt_tracker=tracker,
        receipt_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
        policy_control_client=policy_control_client,
        lending_portfolio_service=StubLendingPortfolioService(),
    )
    item._test_mainnet_rpc = rpc
    return item


class StubPolicyControl:
    def __init__(self, available: bool = True) -> None:
        self.available = available
        self.revision = 0
        self.digest = policy_digest(Policy("2", "1", False, ()).to_dict())
        self.applies = []
        self.capability_changes = []
        self.transfer_enabled = False
        self.lending_enabled = False
        self.authority_state = "READY"
        self.initializations = []
        self.operation_resumes = []
        self.operation_cancels = []

    def status(self):
        if not self.available:
            raise ControlUnavailable("fixture unavailable")
        return {
            "kind": "policy_status", "code": "POLICY_STATUS",
            "policy_revision": self.revision, "policy_digest": self.digest,
            "transfer_authority_enabled": self.transfer_enabled,
            "lending_authority_enabled": self.lending_enabled,
            "authority_state": self.authority_state,
        }

    def apply(self, expected_revision, expected_digest, draft_digest, candidate_digest):
        if not self.available:
            raise ControlUnavailable("fixture unavailable")
        self.applies.append((
            expected_revision, expected_digest, draft_digest, candidate_digest,
        ))
        self.revision += 1
        self.digest = candidate_digest
        return {
            "kind": "policy_applied", "code": "POLICY_REVISION_APPLIED",
            "policy_revision": self.revision, "policy_digest": self.digest,
            "transfer_authority_enabled": False,
            "lending_authority_enabled": False,
            "authority_state": self.authority_state,
        }

    def set_capability(
        self, enabled, expected_revision, expected_digest, draft_digest,
        candidate_digest,
    ):
        self.capability_changes.append((
            enabled, expected_revision, expected_digest, draft_digest, candidate_digest,
        ))
        self.revision += 1
        self.digest = candidate_digest
        self.lending_enabled = enabled
        return {
            "kind": "policy_activated" if enabled else "policy_deactivated",
            "code": "LENDING_AUTHORITY_ENABLED" if enabled else "LENDING_AUTHORITY_DISABLED",
            "policy_revision": self.revision, "policy_digest": self.digest,
            "transfer_authority_enabled": False,
            "lending_authority_enabled": enabled,
            "authority_state": self.authority_state,
        }

    def initialize_authority_state(self, expected_revision, expected_digest):
        self.initializations.append((expected_revision, expected_digest))
        self.authority_state = "READY"
        return {
            "kind": "authority_initialized", "code": "AUTHORITY_STATE_INITIALIZED",
            "policy_revision": self.revision, "policy_digest": self.digest,
            "transfer_authority_enabled": False,
            "lending_authority_enabled": False, "authority_state": "READY",
        }

    def resume_lending_operation(
        self, operation_id, phase_action_id, transaction_hash,
    ):
        self.operation_resumes.append((
            operation_id, phase_action_id, transaction_hash,
        ))
        return {
            "kind": "lending_operation_resumed",
            "code": "LENDING_OPERATION_RESUMED",
        }

    def cancel_lending_operation(self, operation_id):
        self.operation_cancels.append(operation_id)
        return {
            "kind": "lending_operation_cancelled",
            "code": "LENDING_OPERATION_CANCELLED",
        }


def recipient_policy(
    recipient: str,
    recipient_cap: int = 1_000_000,
) -> MainnetBroadcastPolicy:
    return MainnetBroadcastPolicy.from_policy(Policy(
        "2",
        "1",
        True,
        (TransferRule(
            "base",
            "usdc",
            8453,
            "2000000",
            str(10**18),
            (RecipientRule(recipient.lower(), str(recipient_cap)),),
        ),),
    ))


class RecoveryDisplayStub:
    def __init__(self) -> None:
        self.kind = None
        self.value = None

    def set_material(self, kind, value: str) -> None:
        self.kind = kind
        self.value = value

    def clear_material(self) -> None:
        self.kind = None
        self.value = None

    def copy_text(self) -> str | None:
        return self.value


def test_create_persists_only_after_backup_acknowledgement(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()

    assert item.currentScreen == "welcome"
    item.beginCreate()
    assert item.currentScreen == "password"
    assert item.passwordConfirmRequired
    assert not item.submitPassword(secret, secret + "x")
    assert item.submitPassword(secret, secret)
    assert item.currentScreen == "backup"
    assert len(item.backupWords) == 12
    assert not (tmp_path / "wallet-vault.json").exists()
    assert item.finishBackup()
    assert item.currentScreen == "main"
    assert item.backupWords == []
    assert len(item.profiles) == 1
    assert item.profiles[0]["typeLabel"] == "Seed phrase"


def test_confirmed_approval_recovery_is_account_bound_and_cancelled_via_guard(
    tmp_path,
) -> None:
    policy_control = StubPolicyControl()
    item = controller(tmp_path, policy_control)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    profile_id = item.activeProfileId
    address = item.activeProfile["address"]

    operation = LendingOperation(
        operation_id="act-11111111-1111-4111-8111-111111111111",
        requested_action="supply",
        amount_mode="exact",
        amount="2",
        resolved_amount_atomic=2_000_000,
        owner_pid=42,
        policy_version="3",
        policy_revision=7,
        policy_digest="1" * 64,
        action_profile_digest="2" * 64,
        safety_digest="3" * 64,
        phase="resume_or_revoke",
        phase_action_id="act-22222222-2222-4222-8222-222222222222",
        phase_fingerprint="4" * 64,
        created_at="2026-07-26T00:00:00Z",
        account_profile_id=profile_id,
        account_address=address,
        transaction_hash="0x" + "5" * 64,
        receipt_state="confirmed",
        updated_at="2026-07-26T00:01:00Z",
    )
    LendingOperationStore(tmp_path / "lending-operation-state.json").save(
        LendingOperationSnapshot(operation),
    )

    restarted = controller(tmp_path, policy_control)
    assert restarted.currentScreen == "lending_recovery"
    assert restarted.lendingRecovery["amount"] == "2"
    assert restarted.cancelLendingOperation()
    assert restarted.currentScreen == "main"
    assert policy_control.operation_cancels == [operation.operation_id]


def test_trusted_recipients_draft_review_password_restart_and_cancel(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    item.showSettings()
    assert item.showSettingsSection("security")
    item.showTrustedRecipients()
    assert item.currentScreen == "trusted_recipients"
    assert item.trustedDraftAvailable
    assert item.trustedDraftRoutes == []
    assert "transfers remain disabled" in item.trustedDraftStatus

    item.beginTrustedRoute()
    assert item.saveTrustedRoute("base", "usdc", "100", "0.005")
    item.closeTrustedRoute()
    assert not item.showTrustedDraftReview()
    assert item.errorMessage == "Each route requires at least one recipient"
    assert item.editTrustedRoute("base", "usdc")
    assert item.beginTrustedRecipient()
    recipient = "0x" + "ab" * 20
    assert item.saveTrustedRecipient("Savings", recipient, "50")
    route = item.trustedRoute
    assert route["routeAmount"] == "100"
    assert route["routeAmountUsd"] == "≈ $100.00"
    assert route["feeUsd"] == "≈ $12.50"
    assert route["recipients"][0]["label"] == "Savings"
    assert route["recipients"][0]["maxAmountUsd"] == "≈ $50.00"
    assert not item.saveTrustedRoute("ethereum", "eth", "1", "0.005")
    assert item.errorMessage == "Create a new route to change network or asset"

    item.closeTrustedRoute()
    assert item.showTrustedDraftReview()
    assert not item.saveTrustedRoute("base", "usdc", "75", "0.003")
    reviewed = item._trusted_working
    item.beginTrustedDraftPassword()
    item._trusted_working = TrustedPolicyDraft()
    assert not item.submitTrustedDraft(secret)
    assert item.errorMessage == "Draft changed; review it again"
    assert not (tmp_path / "authority-policy-draft.json").exists()
    item._trusted_working = reviewed
    assert item.showTrustedDraftReview()
    item.beginTrustedDraftPassword()
    assert not item.submitTrustedDraft(secret + "-wrong")
    assert item.errorMessage == "Authentication failed"
    assert not (tmp_path / "authority-policy-draft.json").exists()
    assert item.submitTrustedDraft(secret)
    assert item.currentScreen == "trusted_recipients"
    assert not item.trustedDraftDirty
    assert item.trustedDraftStatus == (
        "Draft saved. Transfers remain disabled until policy activation."
    )
    stored = (tmp_path / "authority-policy-draft.json").read_text(encoding="utf-8")
    assert '"transfer_authority_enabled": false' in stored
    assert '"schema_version": "4"' in stored
    assert '"lending_authority_enabled"' not in stored
    assert '"max_amount_atomic": "100000000"' in stored
    assert "$" not in stored and "USD" not in stored

    restarted = controller(tmp_path)
    restarted.showSettings()
    assert restarted.showSettingsSection("security")
    restarted.showTrustedRecipients()
    assert restarted.trustedDraftRoutes[0]["recipients"][0]["label"] == "Savings"
    assert restarted.editTrustedRoute("base", "usdc")
    assert restarted.saveTrustedRoute("base", "usdc", "80", "0.004")
    restarted.closeTrustedRoute()
    assert restarted.trustedDraftDirty
    restarted.closeTrustedRecipients()
    restarted.showTrustedRecipients()
    assert restarted.trustedDraftRoutes[0]["routeAmount"] == "100"
    assert (tmp_path / "authority-policy-draft.json").read_text(encoding="utf-8") == stored

    assert restarted.editTrustedRoute("base", "usdc")
    checksum = restarted.trustedRoute["recipients"][0]["address"]
    assert restarted.beginTrustedRecipient(checksum)
    assert restarted.saveTrustedRecipient("Demo wallet", checksum, "40")
    assert restarted.trustedRoute["recipients"][0]["label"] == "Demo wallet"
    assert restarted.beginTrustedRecipient(checksum)
    assert restarted.deleteTrustedRecipient()
    assert restarted.trustedRoute["recipients"] == []
    assert restarted.deleteTrustedRoute()
    assert restarted.trustedDraftRoutes == []


def test_trusted_draft_apply_has_separate_review_password_and_guard_gate(tmp_path) -> None:
    policy_control = StubPolicyControl()
    item = controller(tmp_path, policy_control)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    draft = TrustedPolicyDraft((TrustedRouteDraft(
        "base", "usdc", 8453, "100000000", "5000000000000000",
        (TrustedRecipientDraft(
            "Savings", "0x4444444444444444444444444444444444444444", "50000000",
        ),),
    ),))
    TrustedPolicyDraftStore(item._repository.paths).save(draft)

    item.showTrustedRecipients()
    assert item.trustedActiveRevision == "Baseline revision 0"
    assert item.trustedCanApply
    assert item.showTrustedApplyReview()
    assert item.currentScreen == "trusted_apply_review"
    item.beginTrustedApplyPassword()
    assert item.currentScreen == "trusted_apply_password"
    assert not item.submitTrustedApply(secret + "-wrong")
    assert policy_control.applies == []
    assert item.submitTrustedApply(secret)
    assert item.currentScreen == "trusted_recipients"
    assert item.trustedActiveRevision == "Active revision 1"
    assert item.trustedDraftMatchesActive
    assert len(policy_control.applies) == 1
    assert "Send and Lending remain disabled" in item.trustedDraftStatus


def test_first_authority_state_initialization_has_review_and_fresh_password(
    tmp_path,
) -> None:
    policy_control = StubPolicyControl()
    policy_control.authority_state = "INITIALIZATION_REQUIRED"
    item = controller(tmp_path, policy_control)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    item.showTrustedRecipients()
    assert item.trustedCanInitializeAuthority
    assert not item.trustedCanApply
    assert item.showTrustedInitializationReview()
    assert item.trustedPolicyOperation == "initialize"
    assert item.currentScreen == "trusted_apply_review"
    item.beginTrustedApplyPassword()
    assert not item.submitTrustedApply(secret + "-wrong")
    assert policy_control.initializations == []
    assert item.submitTrustedApply(secret)

    assert policy_control.initializations == [(0, policy_control.digest)]
    assert item.currentScreen == "trusted_recipients"
    assert not item.trustedCanInitializeAuthority
    assert item.trustedCanApply
    assert item.trustedAuthorityState == "Authority state ready"
    assert "Send and Lending remain disabled" in item.trustedDraftStatus


def test_trusted_apply_rejects_post_review_change_and_unavailable_guard(tmp_path) -> None:
    policy_control = StubPolicyControl()
    item = controller(tmp_path, policy_control)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    store = TrustedPolicyDraftStore(item._repository.paths)
    store.save(TrustedPolicyDraft())
    item.showTrustedRecipients()
    assert item.showTrustedApplyReview()
    item.beginTrustedApplyPassword()
    changed = TrustedPolicyDraft((TrustedRouteDraft(
        "base", "usdc", 8453, "1000000", "1000000000000000",
        (TrustedRecipientDraft(
            "Changed", "0x4444444444444444444444444444444444444444", "1000000",
        ),),
    ),))
    store.save(changed)
    assert not item.submitTrustedApply(secret)
    assert item.errorMessage == "Draft changed; review it again"
    assert policy_control.applies == []

    policy_control.available = False
    item.showTrustedRecipients()
    assert not item.showTrustedApplyReview()
    assert "Guard is unavailable" in item.errorMessage


def test_aave_capability_is_built_in_and_has_no_settings_controls(tmp_path) -> None:
    policy_control = StubPolicyControl()
    item = controller(tmp_path, policy_control)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showTrustedRecipients()
    assert not item.saveTrustedLendingLimits("5", "0.0001")
    assert item.trustedLendingLimits["mode"] == "built_in"
    assert not item.trustedCanActivateLending
    assert not item.trustedCanDeactivateLending
    assert not item.showTrustedCapabilityReview(True)
    assert policy_control.capability_changes == []


def test_corrupt_trusted_draft_does_not_block_public_wallet(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    (tmp_path / "authority-policy-draft.json").write_text("{broken", encoding="utf-8")

    item.showSettings()
    assert item.showSettingsSection("security")
    item.showTrustedRecipients()
    assert not item.trustedDraftAvailable
    assert item.trustedDraftRoutes == []
    item.closeTrustedRecipients()
    item.showMain()
    assert item.currentScreen == "main"
    assert item.portfolioData["assets"]


def test_trusted_draft_cannot_enable_direct_or_guard_transfer(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    recipient = "0x" + "44" * 20
    TrustedPolicyDraftStore(item._repository.paths).save(TrustedPolicyDraft((
        TrustedRouteDraft(
            "base", "usdc", 8453, "100000000", "5000000000000000",
            (TrustedRecipientDraft("Demo", recipient, "50000000"),),
        ),
    )))
    item._mainnet_executor.policy = MainnetBroadcastPolicy.from_policy(
        load_baseline_policy(),
    )

    item.showSend()
    assert not item.prepareTransfer("base", "usdc", recipient, "1")
    assert item.transferError == "Transfers are disabled by local policy"
    assert item._transfer_preflight_service.calls == []

    now = datetime.now(UTC)
    request = {
        "authority_version": "2", "kind": "prepare_transfer",
        "flow_id": "11111111-1111-4111-8111-111111111111",
        "action_id": "act-22222222-2222-4222-8222-222222222222",
        "policy_version": "3", "network": "base", "asset": "usdc",
        "policy_revision": item._mainnet_executor.policy.policy_revision,
        "policy_digest": item._mainnet_executor.policy.policy_digest_value,
        "amount_atomic": "1000000", "recipient": recipient,
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    item.showMain()
    responses = []
    item.prepareExternalTransfer(request, responses.append)
    assert responses[0]["code"] == "POLICY_AUTHORITY_DISABLED"
    assert item._transfer_preflight_service.calls == []
    assert item._test_mainnet_rpc.send_calls == 0


def test_applied_disabled_revision_still_blocks_before_rpc(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    recipient = "0x" + "44" * 20
    baseline = load_baseline_policy()
    store = PolicyRevisionStore(tmp_path, baseline)
    disabled = Policy("2", "1", False, (TransferRule(
        "base", "usdc", 8453, "1000000", str(10**18),
        (RecipientRule(recipient, "1000000"),),
    ),))
    snapshot, _ = store.apply(
        disabled, "a" * 64, 0, BASELINE_POLICY_DIGEST,
    )
    item._mainnet_executor.policy = MainnetBroadcastPolicy.from_snapshot(snapshot, store)
    item.showSend()
    assert not item.prepareTransfer("base", "usdc", recipient, "1")
    assert item.transferError == "Transfers are disabled by local policy"
    assert item._transfer_preflight_service.calls == []


def test_public_restart_opens_main_without_password_session(tmp_path) -> None:
    original = controller(tmp_path)
    secret = password()
    original.beginCreate()
    assert original.submitPassword(secret, secret)
    assert original.finishBackup()
    second = original._repository.new_record(
        import_private_key(raw_private_key()), "Account 2",
    )
    profiles = original._repository.append(secret, second)
    original._replace_profiles(profiles, profiles[0].profile_id)
    assert original.selectProfile(second.summary.profile_id)

    restarted = controller(tmp_path)
    assert restarted.currentScreen == "main"
    assert len(restarted.profiles) == 2
    assert restarted.activeProfile["label"] == "Account 2"
    assert restarted.activeProfileId == second.summary.profile_id
    assert not restarted.passwordConfirmRequired
    assert restarted.passwordTitle == "Enter Password"
    assert not restarted.submitPassword(secret, "")


def test_guard_handoff_lands_on_exact_review_and_edit_rejects(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    now = datetime.now(UTC)
    request = {
        "authority_version": "2",
        "kind": "prepare_transfer",
        "flow_id": "11111111-1111-4111-8111-111111111111",
        "action_id": "act-22222222-2222-4222-8222-222222222222",
        "policy_version": "1",
        "policy_revision": item._mainnet_executor.policy.policy_revision,
        "policy_digest": item._mainnet_executor.policy.policy_digest_value,
        "network": "base",
        "asset": "usdc",
        "amount_atomic": "1000000",
        "recipient": "0x4444444444444444444444444444444444444444",
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    responses = []
    statuses = []
    item.attach_guard_status_sender(statuses.append)
    item.prepareExternalTransfer(request, responses.append)
    assert item.currentScreen == "transfer_review"
    assert item.transferAction["actionId"] == request["action_id"]
    assert item.transferAction["recipient"] == request["recipient"]
    assert responses[0]["kind"] == "transfer_prepared"
    assert responses[0]["prepared_digest"] == item.transferAction["digest"]
    item.editTransfer()
    assert item.currentScreen == "send"
    assert item.transferRecipient == request["recipient"]
    assert item.transferAmountInput == "1"
    assert statuses[0]["event"] == "REJECTED"
    assert statuses[0]["code"] == "TRANSFER_EDITED"


def test_timed_out_external_preflight_cannot_publish_transfer_or_lending(
    tmp_path,
) -> None:
    for kind in ("prepare_transfer", "prepare_lending_action"):
        item = controller(tmp_path / kind)
        secret = password()
        item.beginCreate()
        assert item.submitPassword(secret, secret)
        assert item.finishBackup()
        deferred = DeferredExecutor()
        item._transfer_executor = deferred
        now = datetime.now(UTC).replace(microsecond=0)
        common = {
            "authority_version": "2",
            "kind": kind,
            "flow_id": "11111111-1111-4111-8111-111111111111",
            "action_id": "act-22222222-2222-4222-8222-222222222222",
            "created_at": now.isoformat().replace("+00:00", "Z"),
            "expires_at": (now + timedelta(minutes=5)).isoformat().replace(
                "+00:00", "Z",
            ),
        }
        responses: list[dict[str, object]] = []
        if kind == "prepare_transfer":
            recipient = "0x" + "44" * 20
            item._mainnet_executor.policy = recipient_policy(recipient)
            request = {
                **common,
                "policy_version": "1",
                "policy_revision": item._mainnet_executor.policy.policy_revision,
                "policy_digest": item._mainnet_executor.policy.policy_digest_value,
                "network": "base",
                "asset": "usdc",
                "amount_atomic": "500000",
                "recipient": recipient,
            }
            item.prepareExternalTransfer(request, responses.append, lambda: False)
        else:
            item._mainnet_executor.policy = MainnetBroadcastPolicy.from_policy(
                load_baseline_policy(),
            )
            assert item._mainnet_executor.policy.shared_engine is not None
            request = {
                **common,
                "policy_version": (
                    item._mainnet_executor.policy.shared_engine.policy.policy_version
                ),
                "policy_revision": item._mainnet_executor.policy.policy_revision,
                "policy_digest": item._mainnet_executor.policy.policy_digest_value,
                "action_profile_digest": ACTION_PROFILES_DIGEST,
                "protocol_profile_id": "aave-v3-base-usdc",
                "action": "supply",
                "amount_mode": "exact",
                "amount": "1",
                "resolved_amount_atomic": "1000000",
            }
            item.prepareExternalLending(request, responses.append, lambda: False)

        assert len(deferred.tasks) == 1
        generation = item._transfer_generation
        future, _fn, _args, _kwargs = deferred.tasks.pop()
        future.set_result(object())

        assert responses == []
        assert item.currentScreen == "main"
        assert item.transferAction == {}
        assert item.historyRecords == []
        assert item._transfer_flow.pending is None
        assert item._transfer_flow.current is None
        assert item._external_transfer is None
        assert item._external_completion is None
        assert item._external_begin_delivery is None

        item._accept_transfer_preflight(generation, object())
        assert item.currentScreen == "main"
        assert item.historyRecords == []


def test_confirmed_lending_approve_releases_wallet_before_guard_callback(tmp_path) -> None:
    item = controller(tmp_path)
    observed: list[tuple[str, bool, bool]] = []
    item._external_transfer = {
        "flow_id": "11111111-1111-4111-8111-111111111111",
        "action_id": "act-22222222-2222-4222-8222-222222222222",
        "operation_id": "act-33333333-3333-4333-8333-333333333333",
        "phase_action_id": "act-22222222-2222-4222-8222-222222222222",
        "prepared_digest": "a" * 64,
        "executed_phase": "approve",
    }
    item._external_completion = lambda _response: None
    item._transfer_preparing = True
    item._transfer_recipient = "Aave V3"
    item.attach_guard_status_sender(lambda update: observed.append((
        str(update["event"]), item._transfer_preparing,
        item._transfer_flow.current is not None,
    )))

    item._notify_external_transfer("RECEIPT_CONFIRMED", "CONFIRMED")

    assert observed == [("RECEIPT_CONFIRMED", False, False)]
    assert item._external_transfer is None
    assert item._external_completion is None
    assert item.transferRecipient == ""
    assert item.currentScreen == "main"


def test_guard_handoff_refuses_busy_and_reserved_sender(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    now = datetime.now(UTC)
    request = {
        "authority_version": "2",
        "kind": "prepare_transfer",
        "flow_id": "11111111-1111-4111-8111-111111111111",
        "action_id": "act-22222222-2222-4222-8222-222222222222",
        "policy_version": "1",
        "policy_revision": item._mainnet_executor.policy.policy_revision,
        "policy_digest": item._mainnet_executor.policy.policy_digest_value,
        "network": "base", "asset": "usdc", "amount_atomic": "1000000",
        "recipient": item.activeProfile["address"],
        "created_at": now.isoformat().replace("+00:00", "Z"),
        "expires_at": (now + timedelta(minutes=5)).isoformat().replace("+00:00", "Z"),
    }
    responses = []
    item.prepareExternalTransfer(request, responses.append)
    assert responses[0]["kind"] == "transfer_refused"
    assert responses[0]["code"] == "RESERVED_RECIPIENT"
    item.showSend()
    assert item.prepareTransfer(
        "base", "usdc", "0x5555555555555555555555555555555555555555", "1",
    )
    assert item.currentScreen == "transfer_review"
    request["recipient"] = "0x4444444444444444444444444444444444444444"
    item.prepareExternalTransfer(request, responses.append)
    assert responses[-1]["code"] == "WALLET_BUSY"


def test_recovery_requires_new_exact_action_after_wrong_password(tmp_path) -> None:
    item = controller(tmp_path)
    display = RecoveryDisplayStub()
    item.attach_recovery_display(display)  # type: ignore[arg-type]
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    item.showRecoveryReview()
    assert item.currentScreen == "recovery_review"
    assert item.recoverySelection == "seed_phrase"
    assert item.recoverySeedAvailable
    assert item.prepareRecovery()
    first_id = item.recoveryAction["actionId"]
    assert not item.submitRecovery(password(), True)
    assert item.currentScreen == "recovery_review"
    assert item.recoveryAction == {}
    assert display.value is None

    assert item.prepareRecovery()
    assert item.recoveryAction["actionId"] != first_id
    assert item.submitRecovery(secret, True)
    assert item.currentScreen == "recovery_reveal"
    assert item.recoveryAction == {}
    assert item.recoveryRevealSeconds == 60
    assert display.value is not None and len(display.value.split()) == 12

    item._recovery_reveal_seconds = 1
    item._tick_recovery_reveal()
    assert item.currentScreen == "settings_info"
    assert item.settingsSection == "security"
    assert display.value is None
    assert item.recoveryRevealSeconds == 0


def test_raw_key_recovery_refuses_seed_phrase_and_reveals_only_key(tmp_path) -> None:
    secret = password()
    repository = VaultRepository(WalletPaths(tmp_path))
    repository.create_new(
        secret,
        repository.new_record(import_private_key(raw_private_key()), "Main Account"),
    )
    item = controller(tmp_path)
    display = RecoveryDisplayStub()
    item.attach_recovery_display(display)  # type: ignore[arg-type]
    assert item.currentScreen == "main"

    item.showRecoveryReview()
    assert item.recoverySelection == "private_key"
    assert not item.recoverySeedAvailable
    assert not item.selectRecoveryMaterial("seed_phrase")
    assert item.prepareRecovery()
    assert item.submitRecovery(secret, True)
    assert display.value is not None
    assert display.value.startswith("0x") and len(display.value) == 66
    item.finishRecovery()
    assert display.value is None


def test_first_import_supports_seed_and_existing_vault_adds_only_raw_key(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    mnemonic = generate_mnemonic().value

    item.beginImport()
    assert item.currentScreen == "import"
    assert item.submitImport("seed", mnemonic)
    assert item.submitPassword(secret, secret)
    assert item.activeProfile["label"] == "Main Account"

    item.showWallets()
    item.beginAddPrivateKey()
    assert item.importPrivateOnly
    assert not item.submitImport("seed", mnemonic)
    assert item.submitImport("private", raw_private_key())
    assert item.submitPassword(secret, "")
    assert len(item.profiles) == 2
    assert item.activeProfile["label"] == "Account 2"
    assert item.activeProfile["typeLabel"] == "Private key"


def test_cancel_and_unknown_selection_leave_state_unchanged(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    item.cancelFlow()

    assert item.currentScreen == "welcome"
    assert item.backupWords == []
    assert list(tmp_path.iterdir()) == []
    assert not item.selectProfile("unknown")


def test_unsigned_preflight_writes_public_history_without_authentication(
    tmp_path, monkeypatch,
) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    vault_before = item._repository.paths.vault.read_bytes()

    def forbidden_authentication(_password: str):
        raise AssertionError("Unsigned preflight touched vault authentication")

    monkeypatch.setattr(item._repository, "authenticate", forbidden_authentication)
    item.showSend()
    assert item.currentScreen == "send"
    assert item.prepareTransfer("0x" + "44" * 20)
    assert item.currentScreen == "transfer_review"
    assert item.transferAction["network"] == "Base"
    assert item.transferAction["amount"] == "1 USDC"
    assert item.transferAction["maxTotalFeeWei"].isdigit()
    assert "data" not in item.transferAction
    assert item.historyRecords[0]["status"] == HistoryStatus.PREPARED.value
    assert item.historyRecords[0]["simulated"] is False
    assert item._repository.paths.vault.read_bytes() == vault_before

    item.finishTransfer()
    assert item.currentScreen == "main"
    assert item._transfer_flow.state is TransferFlowState.LOCKED


def test_generalized_draft_binds_network_asset_recipient_and_exact_amount(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    recipient = "0x" + "44" * 20

    item.showSend()
    assert item.transferNetwork == ""
    assert item.transferAsset == ""
    assert item.prepareTransfer("ethereum", "eth", recipient, "0,001")
    assert item.currentScreen == "transfer_review"
    assert item.transferAction["networkId"] == "ethereum"
    assert item.transferAction["assetId"] == "eth"
    assert item.transferAction["amount"] == "0.001 ETH"
    assert item.transferAction["recipient"].endswith("444444")

    item.editTransfer()
    assert item.transferNetwork == "ethereum"
    assert item.transferAsset == "eth"
    assert item.transferAmountInput == "0.001"
    assert item.transferRecipient.endswith("444444")
    assert item.transferAction == {}


def test_configured_amount_cap_refuses_before_rpc(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item._mainnet_executor.policy = MainnetBroadcastPolicy(
        True,
        OfflineSigningPolicy(10**18),
        amount_limits={("base", "usdc"): 999_999},
    )

    item.showSend()
    assert not item.prepareTransfer("base", "usdc", "0x" + "44" * 20, "1")
    assert item.transferError == "Amount exceeds the local recipient or route limit"
    assert item._transfer_preflight_service.calls == []


def test_shared_policy_refuses_unknown_recipient_before_direct_preflight(
    tmp_path,
) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    allowed = "0x" + "44" * 20
    item._mainnet_executor.policy = recipient_policy(allowed, 750_000)

    item.showSend()
    assert not item.prepareTransfer("base", "usdc", "0x" + "55" * 20, "0.5")
    assert item.transferError == "Recipient is not allowed by local policy"
    assert item._transfer_preflight_service.calls == []
    assert item.maximumTransferAmount("base", "usdc", allowed) == "0.75"
    assert item.prepareTransfer("base", "usdc", allowed, "0.5")
    assert len(item._transfer_preflight_service.calls) == 1


def test_wallet_rechecks_guard_policy_version_before_external_preflight(
    tmp_path,
) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    recipient = "0x" + "44" * 20
    item._mainnet_executor.policy = recipient_policy(recipient)
    responses = []
    item.prepareExternalTransfer({
        "authority_version": "2",
        "kind": "prepare_transfer",
        "flow_id": "11111111-1111-4111-8111-111111111111",
        "action_id": "act-22222222-2222-4222-8222-222222222222",
        "policy_version": "2",
        "policy_revision": item._mainnet_executor.policy.policy_revision,
        "policy_digest": item._mainnet_executor.policy.policy_digest_value,
        "network": "base",
        "asset": "usdc",
        "amount_atomic": "500000",
        "recipient": recipient,
        "created_at": "2026-07-24T10:00:00Z",
        "expires_at": "2026-07-24T10:05:00Z",
    }, responses.append)

    assert responses[0]["code"] == "POLICY_VERSION_MISMATCH"
    assert item._transfer_preflight_service.calls == []
    assert item.currentScreen == "main"


def test_wallet_rejects_guard_revision_digest_before_external_preflight(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    recipient = "0x" + "44" * 20
    item._mainnet_executor.policy = recipient_policy(recipient)
    responses = []
    item.prepareExternalTransfer({
        "authority_version": "2", "kind": "prepare_transfer",
        "flow_id": "11111111-1111-4111-8111-111111111111",
        "action_id": "act-22222222-2222-4222-8222-222222222222",
        "policy_version": "1", "policy_revision": 1,
        "policy_digest": "f" * 64,
        "network": "base", "asset": "usdc", "amount_atomic": "500000",
        "recipient": recipient,
        "created_at": "2026-07-24T10:00:00Z",
        "expires_at": "2026-07-24T10:05:00Z",
    }, responses.append)
    assert responses[0]["code"] == "POLICY_REVISION_CHANGED"
    assert item._transfer_preflight_service.calls == []


def test_maximum_amount_uses_token_cap_and_live_native_fee_quote(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    recipient = "0x" + "44" * 20
    ready = []
    item.transferMaximumReady.connect(lambda *values: ready.append(values))
    item._mainnet_executor.policy = MainnetBroadcastPolicy(
        True,
        OfflineSigningPolicy(10**18),
        amount_limits={
            ("base", "usdc"): 2_000_000,
            ("base", "eth"): 2**256 - 1,
        },
    )

    assert item.maximumTransferAmount("base", "usdc", recipient) == "2"
    assert item.maximumTransferAmount("base", "eth", recipient) == ""
    assert item.requestMaximumTransfer("base", "usdc", recipient)
    assert ready[-1] == ("base", "usdc", recipient, "2")

    assert item.requestMaximumTransfer("base", "eth", recipient)
    max_fee_per_gas = 2 * 10_000_000 + 1_000_000
    expected = 10**18 - 60_500 * max_fee_per_gas
    assert ready[-1] == (
        "base", "eth", recipient, format_atomic_amount(expected, 18),
    )
    assert not item.transferMaximumQuoting
    assert not item.requestMaximumTransfer("base", "eth", "invalid")
    assert item.transferError == "Enter a valid EVM recipient address"


def test_mainnet_execution_submits_once_and_updates_public_history(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    vault_before = item._repository.paths.vault.read_bytes()

    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action_id = item.transferAction["actionId"]
    assert item.mainnetExecutionAvailable
    assert item.mainnetFeeLimit.endswith("ETH")
    assert item.beginMainnetExecution()
    assert item.currentScreen == "sign_transfer"
    assert item.submitMainnetExecution(secret)

    assert item.currentScreen == "transfer_result"
    assert item.mainnetResult["code"] == "PENDING"
    assert item.mainnetResult["actionId"] == action_id
    assert item.mainnetResult["transactionHash"].startswith("0x")
    assert item.mainnetResult["recoveredSigner"] == item.activeProfile["address"]
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    assert item.historyRecords[0]["status"] == HistoryStatus.PENDING.value
    assert item.historyRecords[0]["transactionHash"].startswith("0x")
    assert item._test_mainnet_rpc.send_calls == 1
    assert item._repository.paths.vault.read_bytes() == vault_before

    item.finishMainnetExecution()
    assert item.currentScreen == "main"
    assert item.mainnetResult == {}


def test_manual_receipt_check_confirms_exact_public_transfer(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action = item._transfer_flow.current
    assert action is not None
    assert item.beginMainnetExecution()
    assert item.submitMainnetExecution(secret)
    transaction_hash = item.mainnetResult["transactionHash"]
    sender_topic = "0x" + action.sender[2:].lower().rjust(64, "0")
    recipient_topic = "0x" + action.recipient[2:].lower().rjust(64, "0")
    item._test_mainnet_rpc.receipt = {
        "transactionHash": transaction_hash,
        "from": action.sender,
        "to": action.token_contract,
        "status": 1,
        "gasUsed": 45_000,
        "effectiveGasPrice": 12,
        "logs": [
            {
                "address": action.token_contract,
                "topics": [TRANSFER_EVENT_TOPIC, sender_topic, recipient_topic],
                "data": "0x" + action.amount_atomic.to_bytes(32, "big").hex(),
            },
        ],
    }

    assert item.checkMainnetStatus(action.action_id)
    assert item.mainnetResult["code"] == "CONFIRMED"
    assert item.historyRecords[0]["status"] == "confirmed"


def test_wrong_password_cancel_and_late_execution_result_are_terminal(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    first_id = item.transferAction["actionId"]
    assert item.beginMainnetExecution()
    assert item.submitMainnetExecution(password())
    assert item.currentScreen == "transfer_result"
    assert item.mainnetResult["code"] == "AUTHENTICATION_FAILED"
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    item.finishMainnetExecution()

    deferred = DeferredExecutor()
    repository = item._repository
    history = HistoryStore(repository.paths)
    mainnet, tracker, _rpc = mainnet_services(repository, history)
    second = WalletController(
        repository,
        StubPublicDataService(),
        history,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=deferred,
        mainnet_executor=mainnet,
        receipt_tracker=tracker,
        receipt_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    assert second.currentScreen == "main"
    second.showSend()
    assert second.prepareTransfer("0x" + "55" * 20)
    deferred.run_next()
    assert second.currentScreen == "transfer_review"
    assert second.transferAction["actionId"] != first_id
    assert second.beginMainnetExecution()
    assert second.submitMainnetExecution(secret)
    assert second.mainnetExecutionInProgress
    assert not second.canCloseWallet
    second.shutdown()
    deferred.run_next()
    assert second.mainnetResult == {}
    assert second._transfer_flow.state is TransferFlowState.LOCKED


def test_missing_or_exceeded_local_fee_limit_disables_password_flow(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    history = HistoryStore(repository.paths)
    mainnet, tracker, _rpc = mainnet_services(repository, history, enabled=False)
    item = WalletController(
        repository,
        StubPublicDataService(),
        history,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        mainnet_executor=mainnet,
        receipt_tracker=tracker,
        receipt_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    assert not item.mainnetExecutionAvailable
    assert item.mainnetFeeLimit == "Not configured"
    assert item.mainnetGateMessage == "Transfer policy is unavailable"
    assert not item.beginMainnetExecution()
    assert item.currentScreen == "transfer_review"


def test_mutation_expiry_profile_change_and_executor_failure_are_terminal(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action = item._transfer_flow.current
    assert action is not None
    item._transfer_flow._current = replace(
        action,
        transaction=replace(action.transaction, nonce=action.transaction.nonce + 1),
    )
    assert item.beginMainnetExecution()
    assert not item.submitMainnetExecution(secret)
    assert item.currentScreen == "transfer_result"
    assert item.mainnetResult["code"] == "ACTION_INVALID"
    item.finishMainnetExecution()

    item.showSend()
    assert item.prepareTransfer("0x" + "55" * 20)
    assert item.beginMainnetExecution()
    action = item._transfer_flow.current
    assert action is not None
    item._transfer_flow._current = replace(
        action, expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )
    item._expire_transfer()
    assert item.currentScreen == "transfer_result"
    assert item.mainnetResult["code"] == "ACTION_EXPIRED"
    item.finishMainnetExecution()

    second = item._repository.new_record(import_private_key(raw_private_key()), "Account 2")
    profiles = item._repository.append(secret, second)
    item._replace_profiles(profiles, profiles[0].profile_id)
    item.showSend()
    assert item.prepareTransfer("0x" + "66" * 20)
    assert item.beginMainnetExecution()
    assert item.selectProfile(second.summary.profile_id)
    assert item.currentScreen == "main"
    assert item._transfer_flow.state is TransferFlowState.LOCKED

    class FailingExecutor:
        policy = MainnetBroadcastPolicy(True, OfflineSigningPolicy(10**18))

        @staticmethod
        def execute(*_args):
            raise RuntimeError("secret-bearing-internal-canary")

    failing_repository = VaultRepository(WalletPaths(tmp_path / "failing"))
    failing_history = HistoryStore(failing_repository.paths)
    _mainnet, failing_tracker, _rpc = mainnet_services(
        failing_repository, failing_history,
    )
    failing = WalletController(
        failing_repository,
        StubPublicDataService(),
        failing_history,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        mainnet_executor=FailingExecutor(),
        receipt_tracker=failing_tracker,
        receipt_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    failure_password = password()
    failing.beginCreate()
    assert failing.submitPassword(failure_password, failure_password)
    assert failing.finishBackup()
    failing.showSend()
    assert failing.prepareTransfer("0x" + "77" * 20)
    assert failing.beginMainnetExecution()
    assert failing.submitMainnetExecution(failure_password)
    assert failing.currentScreen == "transfer_result"
    assert failing.mainnetResult["code"] == "SIGNING_FAILED"
    assert "canary" not in repr(failing.mainnetResult).lower()


def test_transfer_invalid_edit_and_profile_change_are_terminal(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    item.showSend()
    assert not item.prepareTransfer("not-an-address")
    assert item.transferError == "Enter a valid EVM recipient address"
    assert item.historyRecords == []

    assert item.prepareTransfer("0x" + "44" * 20)
    first_id = item.transferAction["actionId"]
    item.editTransfer()
    assert item.currentScreen == "send"
    assert item.transferRecipient == "0x" + "44" * 20
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    assert item.prepareTransfer(item.transferRecipient)
    assert item.transferAction["actionId"] != first_id

    second = item._repository.new_record(import_private_key(raw_private_key()), "Account 2")
    profiles = item._repository.append(secret, second)
    item._replace_profiles(profiles, profiles[0].profile_id)
    assert item.selectProfile(second.summary.profile_id)
    assert item.currentScreen == "main"
    assert item._transfer_flow.state is TransferFlowState.LOCKED


def test_transfer_failure_is_safe_and_writes_no_history(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    service = StubTransferPreflightService(
        TransferPreflightError(TransferPreflightCode.INSUFFICIENT_USDC),
    )
    item = WalletController(
        repository,
        StubPublicDataService(),
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=service,
        transfer_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
        transfer_policy=MainnetBroadcastPolicy(
            True, OfflineSigningPolicy(10**18),
        ),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()

    assert item.prepareTransfer("0x" + "44" * 20)
    assert item.currentScreen == "send"
    assert item.transferError == "Insufficient USDC for this transfer"
    assert item.historyRecords == []
    assert not repository.paths.history.exists()


def test_cancelled_preflight_ignores_late_response(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    executor = DeferredExecutor()
    item = WalletController(
        repository,
        StubPublicDataService(),
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=executor,
        price_service=StubPriceService(),
        transfer_policy=MainnetBroadcastPolicy(
            True, OfflineSigningPolicy(10**18),
        ),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    assert item.transferPreparing

    item.cancelTransfer()
    assert item.currentScreen == "main"
    executor.run_next()

    assert item.currentScreen == "main"
    assert item.transferAction == {}
    assert item.historyRecords == []
    assert not repository.paths.history.exists()


def test_history_failure_blocks_review_and_preserves_previous_file(
    tmp_path, monkeypatch,
) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    item.finishTransfer()
    before = item._repository.paths.history.read_bytes()

    def failed_append(_record):
        raise StorageError("write-canary")

    monkeypatch.setattr(item._history_store, "append", failed_append)
    item.showSend()
    assert item.prepareTransfer("0x" + "55" * 20)

    assert item.currentScreen == "send"
    assert item.transferError == "History unavailable · transaction was not prepared"
    assert item._transfer_flow.state is TransferFlowState.LOCKED
    assert item._repository.paths.history.read_bytes() == before


def test_prepared_transfer_expiry_returns_to_form(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action = item._transfer_flow.current
    assert action is not None
    item._transfer_flow._current = replace(
        action,
        expires_at=datetime.now(UTC) - timedelta(seconds=1),
    )

    item._expire_transfer()

    assert item.currentScreen == "send"
    assert item.transferError == "Transaction preparation expired"
    assert item._transfer_flow.state is TransferFlowState.LOCKED


def test_public_refresh_filter_and_stale_result_are_safe(tmp_path) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    service = StubPublicDataService()
    item = WalletController(
        repository,
        service,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
    )
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    assert service.calls[-1][2] == ("ethereum", "base")
    assert item.publicDataBanner == "LOCAL WALLET  ·  LIVE PUBLIC DATA"
    assert item.ethereumData["ethValue"] == "1 ETH"
    assert item.baseData["usdcValue"] == "2.5 USDC"
    assert item.selectNetwork("base")
    assert service.calls[-1][2] == ("base",)
    assert item.selectedNetwork == "base"
    assert not item.selectNetwork("arbitrum")

    current = dict(item.baseData)
    stale = public_snapshot("base", eth=99 * 10**18)
    item._accept_public_data(
        item._public_data_generation - 1,
        type("Snapshot", (), {
            "profile_id": item.activeProfileId,
            "address": item.activeProfile["address"],
            "networks": (stale,),
        })(),
    )
    assert item.baseData == current


def test_corrupt_history_degrades_only_history_screen(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()
    item._history_store.path.write_text("not-json", encoding="utf-8")

    item.showHistory()

    assert item.currentScreen == "history"
    assert not item.historyAvailable
    assert item.historyStateLabel == "History unavailable"
    assert item.profiles


def test_public_startup_and_refresh_never_authenticate_or_decrypt_vault(
    tmp_path, monkeypatch,
) -> None:
    repository = VaultRepository(WalletPaths(tmp_path))
    secret = password()
    repository.create_new(
        secret, repository.new_record(generate_mnemonic(), "Main Account"),
    )
    service = StubPublicDataService()

    def forbidden_authentication(_password: str):
        raise AssertionError("Public startup touched vault authentication")

    monkeypatch.setattr(repository, "authenticate", forbidden_authentication)
    item = WalletController(
        repository,
        service,
        public_data_executor=ImmediateExecutor(),
        transfer_preflight_service=StubTransferPreflightService(),
        transfer_executor=ImmediateExecutor(),
        price_service=StubPriceService(),
        transfer_policy=MainnetBroadcastPolicy(
            True, OfflineSigningPolicy(10**18),
        ),
    )
    assert item.currentScreen == "main"
    assert service.calls[-1][2] == ("ethereum", "base")
    assert item.refreshPublicData()
    assert service.calls[-1][0] == item.activeProfileId

    second = item._repository.new_record(import_private_key(raw_private_key()), "Account 2")
    profiles = item._repository.append(secret, second)
    item._replace_profiles(profiles, profiles[0].profile_id)
    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    assert item.currentScreen == "transfer_review"
    item.editTransfer()
    assert item.selectProfile(second.summary.profile_id)
    assert item.currentScreen == "send"
    assert item._transfer_flow.state is TransferFlowState.LOCKED


def test_v2_routes_visibility_and_public_details_are_memory_only(tmp_path) -> None:
    item = controller(tmp_path)
    secret = password()
    item.beginCreate()
    assert item.submitPassword(secret, secret)
    assert item.finishBackup()

    assert item.portfolioData["totalAvailable"] is True
    assert item.portfolioData["totalUsd"] == "$5,005.00"
    assert item.balancesVisible
    item.toggleBalancesVisibility()
    assert not item.balancesVisible

    item.showReceive()
    assert item.currentScreen == "receive"
    assert item.receiveQrSource.endswith(item.activeProfile["address"])
    assert item.selectReceiveNetwork("ethereum")
    assert item.receiveNetwork == "ethereum"
    assert not item.selectReceiveNetwork("arbitrum")

    item.showSettings()
    assert item.currentScreen == "settings"
    assert item.showSettingsSection("security")
    assert item.currentScreen == "settings_info"
    assert item.settingsSection == "security"
    item.closeSettingsInfo()
    item.showWallets()
    assert item.currentScreen == "wallets"
    item.closeWallets()
    assert item.currentScreen == "settings"

    item.showSend()
    assert item.prepareTransfer("0x" + "44" * 20)
    action_id = item.transferAction["actionId"]
    item.cancelTransfer()
    item.showHistory()
    assert item.showTransactionDetails(action_id)
    assert item.currentScreen == "transaction_details"
    assert item.selectedHistoryRecord["actionId"] == action_id
    assert item.selectedHistoryRecord["maxTotalFeeWei"]
    item.closeTransactionDetails()
    assert item.currentScreen == "history"
