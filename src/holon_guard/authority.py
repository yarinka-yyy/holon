"""Contract, policy, journal, request control, and Guard lifecycle boundary."""
from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

from holon_contracts import ContractEnvelope, MessageKind, SecurityCode
from holon_guard_ipc import GuardState
from holon_journal import EventType, JournalFailure
from holon_lending import (
    ActionProfilesState, LendingPortfolioService, LendingReader, LendingReadService,
)
from holon_lending.preflight import unavailable_preview
from holon_modules import CapabilityRegistry, ModuleLifecycleState
from holon_earn import (
    EarnPortfolioService,
    LENDING_PROVIDER_ID,
)
from holon_policy import (
    PolicyEngine, PolicyRevisionStore, PolicyRevisionUnavailable, PolicySnapshot,
    policy_digest,
)

from .authority_audit import AuthorityAudit
from .authority_prepare import prepare
from .authority_intent import prepare_intent
from .lending_authority import prepare_lending_authority
from .module_authority import prepare_module_authority
from .authority_responses import REFUSAL_CODES, ResponseMixin
from .lifecycle import GuardLifecycle
from .wallet import WALLET_OPEN_FAILURE_MESSAGES, wallet_open_failure


class AuthorityService(ResponseMixin):
    refusal_codes = REFUSAL_CODES

    def __init__(
        self, lifecycle: GuardLifecycle, policy: PolicyEngine, audit: AuthorityAudit,
        security_failure: str | None = None,
        policy_snapshot: PolicySnapshot | None = None,
        revision_store: PolicyRevisionStore | None = None,
        lending: LendingReader | None = None,
        lending_actions: ActionProfilesState | None = None,
        lending_portfolio: LendingPortfolioService | None = None,
        earn_portfolio: EarnPortfolioService | None = None,
        lending_history=None,
        module_registry: CapabilityRegistry | None = None,
        module_data_dir: Path | None = None,
    ) -> None:
        self.lifecycle = lifecycle
        self.policy = policy
        self.audit = audit
        self.security_failure = security_failure
        self.policy_snapshot = policy_snapshot or PolicySnapshot(
            0, policy_digest(policy.policy.to_dict()), policy.policy,
        )
        self.revision_store = revision_store
        self.lending = lending or LendingReadService.unavailable()
        self.lending_actions = lending_actions or ActionProfilesState.load()
        self.lending_portfolio = lending_portfolio
        self.earn_portfolio = earn_portfolio
        self.lending_history = lending_history
        self.module_registry = module_registry or CapabilityRegistry()
        if module_data_dir is not None:
            for capability in self.module_registry.capabilities("protected_action_adapter"):
                configure = getattr(capability.contribution, "configure", None)
                if callable(configure):
                    configure(module_data_dir)

    def module_action_adapter(
        self, module_id: str, capability_id: str, action_type: str,
    ):
        status = self.module_registry.module_status(module_id)
        capability = self.module_registry.resolve(capability_id)
        descriptor = capability.declaration.descriptor
        profiles = {
            "hyperliquid-mainnet-v1": f"{module_id}.action.wallet",
            "hyperliquid-arbitrum-funding-v1": f"{module_id}.funding.wallet",
        }
        profile_id = descriptor.get("profile_id")
        if (
            status.state is not ModuleLifecycleState.READY
            or capability.module_id != module_id
            or capability.declaration.kind != "protected_action_adapter"
            or descriptor.get("adapter_version") != "1"
            or profile_id not in profiles
            or action_type not in descriptor.get("action_types", ())
            or getattr(capability.contribution, "adapter_version", None) != "1"
            or getattr(capability.contribution, "profile_id", None) != profile_id
            or not isinstance(
                getattr(capability.contribution, "wallet_capability_id", None), str,
            )
            or getattr(capability.contribution, "wallet_capability_id", None)
            != profiles[profile_id]
        ):
            raise RuntimeError("Module action adapter is unavailable")
        return capability, capability.contribution

    @staticmethod
    def _module_preview_payload(
        module_id: str, capability_id: str, action_type: str, *,
        preview=None, status: str = "UNAVAILABLE", caveat: str = "CAPABILITY_UNAVAILABLE",
        execution_available: bool = False,
    ) -> dict[str, object]:
        if preview is None:
            return {
                "status": status, "authority_available": False,
                "execution_available": False, "module_id": module_id,
                "capability_id": capability_id, "action_type": action_type,
                "account": None, "preview": {}, "preview_digest": None,
                "expires_at": None, "checks": [], "caveats": [caveat],
                "code": (
                    "MODULE_ACTION_REFUSED" if status == "REFUSED"
                    else "MODULE_ACTION_UNAVAILABLE"
                ),
                "message": "PerpDEX action preview is unavailable.",
            }
        return {
            "status": preview.status,
            "authority_available": execution_available,
            "execution_available": execution_available,
            "module_id": module_id,
            "capability_id": capability_id,
            "action_type": preview.action_type.value,
            "account": dict(preview.account) if preview.account is not None else None,
            "preview": dict(preview.preview),
            "preview_digest": preview.preview_digest,
            "expires_at": preview.expires_at,
            "checks": list(preview.checks),
            "caveats": list(preview.caveats),
            "code": preview.code,
            "message": preview.message,
        }

    def replace_policy_snapshot(self, snapshot: PolicySnapshot) -> None:
        self.policy_snapshot = snapshot
        self.policy = PolicyEngine(snapshot.policy)

    def revalidate_policy(self) -> bool:
        if self.revision_store is None or self.policy_snapshot is None:
            return True
        try:
            current = self.revision_store.load()
        except PolicyRevisionUnavailable:
            self.fail_closed(SecurityCode.POLICY_STATE_INVALID.value)
            return False
        if (
            current.policy_revision != self.policy_snapshot.policy_revision
            or current.policy_digest != self.policy_snapshot.policy_digest
        ):
            self.fail_closed(SecurityCode.POLICY_REVISION_CHANGED.value)
            return False
        return True

    def fail_closed(self, code: str, record_event: bool = True) -> None:
        self.security_failure = code
        if self.lifecycle.snapshot.state in {
            GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING,
        }:
            self.lifecycle.interrupt_for_security_block(code)
        elif self.lifecycle.snapshot.state is not GuardState.RECOVERY_REQUIRED:
            self.lifecycle.disable_signing(code)
        if record_event and not code.startswith("JOURNAL_"):
            try:
                self.audit.event(
                    EventType.SIGNING_DISABLED, code,
                    guard_state=self.lifecycle.snapshot.state.value,
                )
            except JournalFailure as exc:
                self.security_failure = exc.code

    def security_response(self, request: ContractEnvelope):
        code = self.security_failure or "SIGNING_DISABLED"
        if (
            self.lifecycle.snapshot.state is GuardState.RECOVERY_REQUIRED
            and (
                self.lifecycle.ledger.find(request.action_id or "") is not None
                or (
                    self.lifecycle.lending_operation_snapshot.current is not None
                    and self.lifecycle.lending_operation_snapshot.current.operation_id
                    == (request.action_id or "")
                )
            )
        ):
            return self._status(request, MessageKind.RECOVERY_REQUIRED, code)
        return self._signing_disabled(request, code)

    def fail_closed_response(self, request: ContractEnvelope, code: str):
        self.fail_closed(code)
        return self.security_response(request)

    def audit_transfer(self, event_type, code: str, request: ContractEnvelope, **extra) -> bool:
        try:
            self.audit.transfer(event_type, code, request, **extra)
            return True
        except JournalFailure as exc:
            self.fail_closed(exc.code, record_event=False)
            return False

    def audit_system(self, event_type, code: str, **fields) -> bool:
        try:
            self.audit.event(event_type, code, **fields)
            return True
        except JournalFailure as exc:
            self.fail_closed(exc.code, record_event=False)
            return False

    def audit_monitor(self, result, action_id: str | None, flow_id: str | None) -> None:
        if result.code == "ACTION_CANCELLED" and action_id is not None:
            event_type = EventType.ACTION_CANCELLED
        elif result.state is GuardState.RECOVERY_REQUIRED and action_id is not None:
            event_type = EventType.RECOVERY_REQUIRED
        elif result.state is GuardState.SIGNING_DISABLED:
            event_type = EventType.SIGNING_DISABLED
        else:
            return
        fields = {"guard_state": result.state.value}
        if action_id is not None:
            fields["action_id"] = action_id
        if flow_id is not None:
            fields["flow_id"] = flow_id
        self.audit_system(event_type, result.code, **fields)

    def accept_wallet_status(self, update: dict[str, object]) -> bool:
        context = self.lifecycle.prepared_audit_context
        if context is None or not self.lifecycle.accept_wallet_status(update):
            return False
        if context.get("module_id") is not None:
            common = {
                "action_id": str(update["action_id"]),
                "flow_id": str(update["flow_id"]),
                "action_type": str(context["action_type"]),
                "wallet_address": str(context["wallet_address"]),
            }
            event = str(update["event"])
            if event == "REJECTED":
                return self.audit_system(
                    EventType.LOCAL_REJECTED, str(update["code"]), **common,
                )
            if event == "COMPLETED":
                if context.get("local_approved_recorded") is not True:
                    if not self.audit_system(
                        EventType.LOCAL_APPROVED, "LOCAL_APPROVED", **common,
                    ):
                        return False
                    context["local_approved_recorded"] = True
                return self.audit_system(
                    EventType.BROADCAST_RESULT, str(update["code"]), **common,
                )
            if event == "FAILED":
                for field in (
                    "stage", "failure_category", "operation_class",
                    "external_submission_started",
                ):
                    value = update.get(field)
                    if value is not None:
                        common[field] = value
                return self.audit_system(
                    EventType.TECHNICAL_ERROR, str(update["code"]), **common,
                )
            return False
        common = {
            "action_id": str(update["action_id"]),
            "flow_id": str(update["flow_id"]),
            "action_type": str(context["action_type"]),
            "network": str(context["network"]),
            "wallet_address": str(context["wallet_address"]),
            "recipient": str(context["recipient"]),
            "asset": str(context["asset"]),
            "amount_atomic": str(context["amount_atomic"]),
        }
        event = str(update["event"])
        if event == "REJECTED":
            return self.audit_system(
                EventType.LOCAL_REJECTED, str(update["code"]), **common,
            )
        if event in {"BROADCASTED", "COMPLETED"}:
            if context.get("local_approved_recorded") is not True:
                if not self.audit_system(
                    EventType.LOCAL_APPROVED, "LOCAL_APPROVED", **common,
                ):
                    return False
                context["local_approved_recorded"] = True
            if (
                context.get("selector") is not None
                and context.get("contract_action_recorded") is not True
            ):
                if not self.audit_system(
                    EventType.CONTRACT_ACTION, "CONTRACT_ACTION", **common,
                    contract=str(context["contract"]),
                    selector=str(context["selector"]),
                    calldata_hash=str(context["calldata_hash"]),
                ):
                    return False
                context["contract_action_recorded"] = True
            broadcast = dict(common)
            if update.get("transaction_hash") is not None:
                broadcast["transaction_hash"] = str(update["transaction_hash"])
            return self.audit_system(
                EventType.BROADCAST_RESULT, str(update["code"]), **broadcast,
            )
        if event in {"RECEIPT_CONFIRMED", "RECEIPT_FAILED"}:
            broadcast = dict(common)
            if update.get("transaction_hash") is not None:
                broadcast["transaction_hash"] = str(update["transaction_hash"])
            return self.audit_system(
                EventType.BROADCAST_RESULT, str(update["code"]), **broadcast,
            )
        if event == "FAILED":
            return self.audit_system(
                EventType.TECHNICAL_ERROR, str(update["code"]), **common,
            )
        return False

    def _recover(self, request: ContractEnvelope):
        result = self.lifecycle.recover_flow(request.action_id or "")
        if not result.ok:
            return self._failure(request, result)
        if not self.audit_system(
            EventType.RECOVERY_COMPLETED, result.code, action_id=request.action_id,
            guard_state=result.state.value,
        ):
            return self.security_response(request)
        return self._status(request, MessageKind.ACTION_STATUS, result.code)

    def handle(self, request: ContractEnvelope, owner_pid: int | None) -> ContractEnvelope:
        if request.kind is MessageKind.HEALTH_REQUEST:
            return self._health(request)
        if request.kind is MessageKind.OPEN_WALLET:
            try:
                result = self.lifecycle.wallet.open_public()
            except Exception:
                if not self.audit_system(
                    EventType.TECHNICAL_ERROR, "WALLET_OPEN_INTERNAL_FAILURE",
                ):
                    return self.security_response(request)
                failure = wallet_open_failure("WALLET_OPEN_INTERNAL_FAILURE")
                return self.error(request, failure.code, failure.message)
            if (
                result is None
                or not result.ok
                or result.wallet_state not in {"OPENED", "ACTIVATED"}
            ):
                if result is not None and result.code in WALLET_OPEN_FAILURE_MESSAGES:
                    failure = wallet_open_failure(result.code, result.exit_code)
                    return self.error(request, failure.code, failure.message)
                return self.error(
                    request,
                    "WALLET_UNAVAILABLE",
                    "Wallet is unavailable.",
                )
            return self._response(
                request,
                MessageKind.WALLET_OPENED,
                {
                    "guard_state": self.lifecycle.snapshot.state.value,
                    "authority_available": False,
                    "wallet_state": result.wallet_state,
                    "code": result.code,
                    "message": (
                        "Wallet activation was requested."
                        if result.wallet_state == "ACTIVATED"
                        else "Wallet launch was verified."
                    ),
                },
            )
        if request.kind is MessageKind.READ_WALLET_BALANCES:
            try:
                result = self.lifecycle.wallet.read_public_balances()
            except Exception:
                result = None
            if result is None or not result.ok or result.payload is None:
                return self.error(
                    request,
                    "WALLET_BALANCES_UNAVAILABLE",
                    "Wallet balances are unavailable.",
                )
            return self._response(
                request,
                MessageKind.WALLET_BALANCES,
                result.payload,
            )
        if request.kind is MessageKind.MODULE_READ_REQUEST:
            module_id = str(request.payload["module_id"])
            capability_id = str(request.payload["capability_id"])
            operation = str(request.payload["operation"])
            try:
                status = self.module_registry.module_status(module_id)
                capability = self.module_registry.resolve(capability_id)
                if (
                    status.state is not ModuleLifecycleState.READY
                    or capability.module_id != module_id
                    or capability.declaration.kind != "public_reader"
                    or operation not in capability.declaration.descriptor["operations"]
                    or not callable(capability.contribution)
                ):
                    raise RuntimeError("Module read is unavailable")
                params = dict(request.payload["params"])
                account_operations = getattr(
                    capability.contribution, "ACCOUNT_OPERATIONS", (),
                )
                if operation in account_operations:
                    wallet_result = self.lifecycle.wallet.read_public_balances()
                    account = (
                        wallet_result.payload.get("account")
                        if wallet_result.ok and wallet_result.payload is not None
                        else None
                    )
                    if not isinstance(account, Mapping):
                        raise RuntimeError("Wallet account is unavailable")
                    params["active_account"] = dict(account)
                result = capability.contribution(operation, params)
                if not isinstance(result, Mapping):
                    raise RuntimeError("Module result is invalid")
                payload = {
                    "status": "READY",
                    "module_id": module_id,
                    "capability_id": capability_id,
                    "operation": operation,
                    "result": dict(result),
                    "code": "MODULE_READ_READY",
                    "message": "Optional module read completed.",
                }
                return self._response(
                    request, MessageKind.MODULE_READ_RESPONSE, payload,
                )
            except Exception:
                payload = {
                    "status": "UNAVAILABLE",
                    "module_id": module_id,
                    "capability_id": capability_id,
                    "operation": operation,
                    "result": {},
                    "code": "CAPABILITY_UNAVAILABLE",
                    "message": "Optional module read is unavailable.",
                }
            return self._response(request, MessageKind.MODULE_READ_RESPONSE, payload)
        if request.kind is MessageKind.MODULE_ACTION_INTENT:
            module_id = str(request.payload["module_id"])
            capability_id = str(request.payload["capability_id"])
            action_type = str(request.payload["action_type"])
            recovery_exit = (
                self.lifecycle.snapshot.state is GuardState.RECOVERY_REQUIRED
                and action_type in {"CLOSE_POSITION", "HLP_WITHDRAW"}
            )
            if self.lifecycle.snapshot.state in {
                GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING,
            } or (
                self.lifecycle.snapshot.state is GuardState.RECOVERY_REQUIRED
                and not recovery_exit
            ):
                payload = self._module_preview_payload(
                    module_id, capability_id, action_type,
                    status="REFUSED", caveat="PROTECTED_FLOW_ACTIVE",
                )
            else:
                try:
                    _capability, adapter = self.module_action_adapter(
                        module_id, capability_id, action_type,
                    )
                    wallet = self.lifecycle.wallet.read_public_balances()
                    account = (
                        wallet.payload.get("account")
                        if wallet.ok and wallet.payload is not None else None
                    )
                    if not isinstance(account, Mapping):
                        raise RuntimeError("Wallet account is unavailable")
                    preview = adapter.preview(
                        action_type, request.payload["params"], dict(account),
                    )
                    execution_available = (
                        self.security_failure is None
                        and (
                            self.lifecycle.snapshot.state is GuardState.NORMAL
                            or recovery_exit
                        )
                    )
                    payload = self._module_preview_payload(
                        module_id, capability_id, action_type,
                        preview=preview, execution_available=execution_available,
                    )
                except Exception as exc:
                    caveat = str(getattr(exc, "code", "CAPABILITY_UNAVAILABLE"))
                    status = (
                        "REFUSED"
                        if caveat.startswith(("FUNDING_", "HLP_", "PERPDEX_"))
                        and not caveat.endswith(("UNAVAILABLE", "INVALID"))
                        else "UNAVAILABLE"
                    )
                    payload = self._module_preview_payload(
                        module_id, capability_id, action_type,
                        status=status, caveat=caveat,
                    )
            return self._response(request, MessageKind.MODULE_ACTION_PREVIEW, payload)
        if request.kind is MessageKind.MODULE_AUTHORITY_INTENT:
            if not self.revalidate_policy() or self.security_failure is not None:
                return self.security_response(request)
            assert owner_pid is not None
            return prepare_module_authority(self, request, owner_pid)
        if request.kind is MessageKind.MODULE_ACTION_STATUS_REQUEST:
            module_id = str(request.payload["module_id"])
            capability_id = str(request.payload["capability_id"])
            try:
                adapter = None
                for action_type in ("HLP_WITHDRAW", "FUND_TRADING_ACCOUNT"):
                    try:
                        _capability, adapter = self.module_action_adapter(
                            module_id, capability_id, action_type,
                        )
                        break
                    except RuntimeError:
                        continue
                if adapter is None:
                    raise RuntimeError("Module action adapter is unavailable")
                operation = adapter.status(request.action_id or "")
                if not isinstance(operation, Mapping):
                    raise RuntimeError("Operation is unavailable")
                phases = [{
                    "cloid": phase.get("cloid"), "code": phase.get("code"),
                    "phase_id": phase.get("phase_id"),
                    "phase_type": phase.get("phase_type"),
                    "public_id": phase.get("public_id"), "state": phase.get("state"),
                } for phase in operation["phases"]]
                payload = {
                    "module_id": module_id, "capability_id": capability_id,
                    "action_type": operation["action_type"],
                    "operation_id": operation["operation_id"],
                    "operation_state": operation["state"], "phases": phases,
                    "code": "MODULE_ACTION_STATUS_READY",
                    "message": "PerpDEX operation status is available.",
                }
                return self._response(request, MessageKind.MODULE_ACTION_STATUS, payload)
            except Exception:
                return self.refusal(
                    request, RefusalCode.ACTION_ID_INVALID.value,
                    "Module action was not found.",
                )
        if request.kind is MessageKind.READ_LENDING_MARKETS:
            try:
                payload = self.lending.compare(request.payload.get("force_refresh", False))
            except Exception:
                payload = LendingReadService.unavailable().compare(
                    request.payload.get("force_refresh", False),
                )
            return self._response(request, MessageKind.LENDING_MARKETS, payload)
        if request.kind is MessageKind.READ_LENDING_POSITIONS:
            account = None
            try:
                result = self.lifecycle.wallet.read_public_balances()
                if result.ok and result.payload is not None:
                    account = result.payload.get("account")
                payload = self.lending.positions(account)
            except Exception:
                payload = LendingReadService.unavailable().positions(None)
            return self._response(request, MessageKind.LENDING_POSITIONS, payload)
        if request.kind is MessageKind.READ_LENDING_PORTFOLIO:
            account = None
            operations = None
            try:
                result = self.lifecycle.wallet.read_public_balances()
                if result.ok and result.payload is not None:
                    account = result.payload.get("account")
                if account is not None and callable(self.lending_history):
                    operations = self.lending_history(account["address"])
                if self.lending_portfolio is None:
                    raise RuntimeError("Lending portfolio is unavailable")
                payload = self.lending_portfolio.read(
                    account,
                    operations,
                    force_refresh=request.payload.get("force_refresh", False),
                    history_period=request.payload.get("history_period", "none"),
                    history_limit=12,
                )
            except Exception:
                payload = LendingPortfolioService.unavailable(
                    account, request.payload.get("history_period", "none"),
                )
            return self._response(request, MessageKind.LENDING_PORTFOLIO, payload)
        if request.kind is MessageKind.READ_EARN_PORTFOLIO:
            account = None
            operations = None
            try:
                result = self.lifecycle.wallet.read_public_balances()
                if result.ok and result.payload is not None:
                    account = result.payload.get("account")
                if account is not None and callable(self.lending_history):
                    operations = self.lending_history(account["address"])
                if self.lending_portfolio is None or self.earn_portfolio is None:
                    raise RuntimeError("Earn portfolio is unavailable")
                lending = self.lending_portfolio.read(
                    account, operations,
                    force_refresh=request.payload.get("force_refresh", False),
                    history_period="none", history_limit=0,
                )
                payload = self.earn_portfolio.read(
                    account,
                    provider_contexts={
                        LENDING_PROVIDER_ID: {"lending_payload": lending},
                    },
                    force_refresh=request.payload.get("force_refresh", False),
                ).to_dict()
            except Exception:
                payload = EarnPortfolioService.unavailable(account).to_dict()
            return self._response(request, MessageKind.EARN_PORTFOLIO, payload)
        if request.kind is MessageKind.LENDING_ACTION_INTENT:
            action = request.payload.get("action")
            mode = request.payload.get("amount_mode")
            profile_id = str(request.payload.get("protocol_profile_id", ""))
            profile = self.lending_actions.select(
                profile_id,
            )
            if self.lifecycle.snapshot.state in {
                GuardState.ENTERING, GuardState.ACTIVE, GuardState.EXITING,
                GuardState.RECOVERY_REQUIRED,
            }:
                payload = unavailable_preview(
                    "PROTECTED_FLOW_ACTIVE", requested_action=str(action),
                    amount_mode=str(mode), profile_id=profile_id,
                )
            elif profile is None:
                payload = unavailable_preview(
                    self.lending_actions.error_code or "ACTION_PROFILES_UNAVAILABLE",
                    requested_action=str(action), amount_mode=str(mode),
                    profile_id=profile_id,
                )
            else:
                try:
                    result = self.lifecycle.wallet.preview_lending(
                        request.payload, profile.digest,
                    )
                    payload = (
                        result.payload if result.ok and result.payload is not None
                        else unavailable_preview(
                            "WALLET_UNAVAILABLE", requested_action=str(action),
                            amount_mode=str(mode),
                            profile_digest=profile.digest,
                            profile_id=profile_id,
                        )
                    )
                except Exception:
                    payload = unavailable_preview(
                        "WALLET_UNAVAILABLE", requested_action=str(action),
                        amount_mode=str(mode),
                        profile_digest=profile.digest,
                        profile_id=profile_id,
                    )
            return self._response(request, MessageKind.LENDING_ACTION_PREVIEW, payload)
        if request.kind is MessageKind.LENDING_AUTHORITY_INTENT:
            if not self.revalidate_policy() or self.security_failure is not None:
                return self.security_response(request)
            assert owner_pid is not None
            return prepare_lending_authority(self, request, owner_pid)
        if request.kind is MessageKind.PREPARE_TRANSFER:
            if self.security_failure is not None:
                return self.security_response(request)
            assert owner_pid is not None
            return prepare(self, request, owner_pid)
        if request.kind is MessageKind.TRANSFER_INTENT:
            if not self.revalidate_policy():
                return self.security_response(request)
            if self.security_failure is not None:
                return self.security_response(request)
            if self.lifecycle.snapshot.state is GuardState.SIGNING_DISABLED:
                return self._signing_disabled(
                    request,
                    self.lifecycle.snapshot.reason
                    if self.lifecycle.snapshot.reason in {
                        "POLICY_AUTHORITY_DISABLED", "SIGNING_DISABLED",
                    }
                    else "SIGNING_DISABLED",
                )
            assert owner_pid is not None
            return prepare_intent(self, request, owner_pid)
        if request.kind is MessageKind.ACTION_STATUS_REQUEST:
            return self._status(request, MessageKind.ACTION_STATUS, "ACTION_STATUS")
        if request.kind is MessageKind.CANCEL_ACTION:
            result = self.lifecycle.cancel_flow(request.action_id or "")
            if result.state is GuardState.RECOVERY_REQUIRED and not self.audit_system(
                EventType.RECOVERY_REQUIRED, result.code, action_id=request.action_id,
                guard_state=result.state.value, flow_id=result.flow_id,
            ):
                return self.security_response(request)
            return self._failure(request, result) if not result.ok else self._status(
                request, MessageKind.ACTION_STATUS, result.code
            )
        return self._recover(request)
