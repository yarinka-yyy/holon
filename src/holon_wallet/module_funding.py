"""Core-owned one-shot execution for a verified optional EVM funding bundle."""

from __future__ import annotations

from datetime import UTC, datetime

from .broadcast import MainnetTransferCode, MainnetTransferExecutor
from .history import HistoryStatus, WalletHistoryRecord
from .perpdex_action import PerpDexExecutionResult
from .transfer import SigningPermit


class ModuleFundingExecutor:
    def __init__(self, mainnet: MainnetTransferExecutor, adapter: object) -> None:
        self._mainnet = mainnet
        self._adapter = adapter

    def execute(self, prepared: object, password: str) -> PerpDexExecutionResult:
        bundle = prepared.bundle
        action = prepared.action
        phase = bundle.phases[0]
        try:
            self._adapter.mark_operation(bundle.operation_id, "EXECUTING")
            self._adapter.mark_phase(bundle.operation_id, phase.phase_id, "SUBMITTING", code="SUBMITTING")
            self._mainnet.history_store.append(WalletHistoryRecord(
                action_id=action.action_id, profile_id=action.profile_id, action_type="transfer",
                network=action.network_id, chain_id=action.chain_id, sender=action.sender,
                recipient=action.recipient, contract=action.token_contract, token=action.token,
                amount_atomic=str(action.amount_atomic), decimals=action.decimals,
                transaction_hash=None, status=HistoryStatus.PREPARED,
                created_at=_timestamp(action.created_at), updated_at=_timestamp(action.created_at),
                simulated=False, max_total_fee_wei=str(action.max_total_fee_wei),
                operation_id=bundle.operation_id,
            ))
        except Exception:
            return self._terminal(bundle, phase, "FAILED", "FUNDING_HISTORY_UNAVAILABLE", None)
        result = self._mainnet.execute(action, action.digest, password, SigningPermit())
        if result.code in {
            MainnetTransferCode.CONFIRMED, MainnetTransferCode.PENDING,
        }:
            return self._terminal(
                bundle, phase, "PENDING_CREDIT", "FUNDING_BROADCAST_PENDING",
                result.transaction_hash,
            )
        if result.code is MainnetTransferCode.UNKNOWN:
            return self._terminal(bundle, phase, "UNKNOWN", "FUNDING_RESULT_UNKNOWN", result.transaction_hash)
        return self._terminal(
            bundle, phase, "FAILED", "FUNDING_" + result.code.value,
            result.transaction_hash or None,
        )

    def _terminal(self, bundle, phase, state: str, code: str, public_id: str | None):
        try:
            self._adapter.mark_phase(bundle.operation_id, phase.phase_id, state, code=code, public_id=public_id)
            self._adapter.mark_operation(bundle.operation_id, state)
        except Exception:
            state, code, public_id = "UNKNOWN", "FUNDING_OPERATION_STATE_UNKNOWN", None
        return PerpDexExecutionResult(
            bundle.operation_id, bundle.intent.action_type.value, state, code,
            "No transaction was retried; refresh the public Hyperliquid portfolio for credit status.",
            ({"phaseId": phase.phase_id, "phaseType": phase.phase_type.value,
              "state": state, "code": code, "publicId": public_id},),
        )


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
