"""Plugin-side Guard boundary and bounded process launcher."""

from __future__ import annotations

from typing import Protocol

from holon_contracts import ContractEnvelope

from holon_guard_ipc import (
    PROTECTED_STATES,
    GuardAvailability,
    GuardHealth,
    GuardState,
    PipeGuardClient,
)

from .launcher import (
    DisabledGuardLauncher, InstalledGuardLauncher, SubprocessGuardLauncher,
    production_launcher,
)


class GuardClient(Protocol):
    def probe(self) -> GuardHealth: ...

    def open_wallet(self) -> ContractEnvelope: ...

    def wallet_balances(self) -> ContractEnvelope: ...

    def lending_markets(self, force_refresh: bool = False) -> ContractEnvelope: ...

    def lending_positions(self) -> ContractEnvelope: ...

    def lending_portfolio(
        self, force_refresh: bool = False, history_period: str = "none",
    ) -> ContractEnvelope: ...

    def lending_action_preview(
        self, payload: dict[str, object],
    ) -> ContractEnvelope: ...

    def lending_action_execute(
        self, payload: dict[str, object], action_id: str,
    ) -> ContractEnvelope: ...

    def prepare_transfer(self, payload: dict[str, str], action_id: str) -> ContractEnvelope: ...

    def transfer_status(self, action_id: str) -> ContractEnvelope: ...

    def cancel_transfer(self, action_id: str) -> ContractEnvelope: ...

    def recover_transfer(self, action_id: str) -> ContractEnvelope: ...


class GuardLauncher(Protocol):
    def start(self) -> None: ...


class UnavailableGuardClient:
    def probe(self) -> GuardHealth:
        return GuardHealth.unavailable()

    def open_wallet(self) -> ContractEnvelope:
        raise RuntimeError("Guard is unavailable")

    def wallet_balances(self) -> ContractEnvelope:
        raise RuntimeError("Guard is unavailable")

    def lending_markets(self, force_refresh: bool = False) -> ContractEnvelope:
        del force_refresh
        raise RuntimeError("Guard is unavailable")

    def lending_positions(self) -> ContractEnvelope:
        raise RuntimeError("Guard is unavailable")

    def lending_portfolio(
        self, force_refresh: bool = False, history_period: str = "none",
    ) -> ContractEnvelope:
        del force_refresh, history_period
        raise RuntimeError("Guard is unavailable")

    def lending_action_preview(
        self, payload: dict[str, object],
    ) -> ContractEnvelope:
        del payload
        raise RuntimeError("Guard is unavailable")

    def lending_action_execute(
        self, payload: dict[str, object], action_id: str,
    ) -> ContractEnvelope:
        del payload, action_id
        raise RuntimeError("Guard is unavailable")

    def prepare_transfer(self, payload: dict[str, str], action_id: str) -> ContractEnvelope:
        del payload, action_id
        raise RuntimeError("Guard is unavailable")

    def transfer_status(self, action_id: str) -> ContractEnvelope:
        del action_id
        raise RuntimeError("Guard is unavailable")

    def cancel_transfer(self, action_id: str) -> ContractEnvelope:
        del action_id
        raise RuntimeError("Guard is unavailable")

    def recover_transfer(self, action_id: str) -> ContractEnvelope:
        del action_id
        raise RuntimeError("Guard is unavailable")


class GuardConnector:
    """Probe, optionally launch once, then probe once more."""

    def __init__(self, client: GuardClient, launcher: GuardLauncher) -> None:
        self._client = client
        self._launcher = launcher

    @staticmethod
    def _normalize(result: object) -> GuardHealth:
        if not isinstance(result, GuardHealth):
            return GuardHealth.uncertain()
        if result.availability is GuardAvailability.AVAILABLE:
            if result.state is GuardState.UNKNOWN:
                return GuardHealth.uncertain()
            return GuardHealth.available(result.state)
        if result.availability is GuardAvailability.UNAVAILABLE:
            return GuardHealth.unavailable()
        if result.availability is GuardAvailability.UNCERTAIN:
            return GuardHealth.uncertain()
        return GuardHealth.uncertain()

    def probe(self) -> GuardHealth:
        try:
            result = self._client.probe()
        except Exception:
            return GuardHealth.uncertain()
        return self._normalize(result)

    def ensure_available(self) -> GuardHealth:
        first = self.probe()
        if first.availability is not GuardAvailability.UNAVAILABLE:
            return first
        try:
            self._launcher.start()
        except Exception:
            return GuardHealth.unavailable()
        return self.probe()

    def open_wallet(self) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.open_wallet()

    def wallet_balances(self) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.wallet_balances()

    def lending_markets(self, force_refresh: bool = False) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.lending_markets(force_refresh)

    def lending_positions(self) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.lending_positions()

    def lending_portfolio(
        self, force_refresh: bool = False, history_period: str = "none",
    ) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.lending_portfolio(force_refresh, history_period)

    def lending_action_preview(
        self, payload: dict[str, object],
    ) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.lending_action_preview(payload)

    def lending_action_execute(
        self, payload: dict[str, object], action_id: str,
    ) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.lending_action_execute(payload, action_id)

    def prepare_transfer(
        self, payload: dict[str, str], action_id: str,
    ) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.prepare_transfer(payload, action_id)

    def transfer_status(self, action_id: str) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.transfer_status(action_id)

    def cancel_transfer(self, action_id: str) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.cancel_transfer(action_id)

    def recover_transfer(self, action_id: str) -> ContractEnvelope:
        health = self.ensure_available()
        if health.availability is not GuardAvailability.AVAILABLE:
            raise RuntimeError("Guard is unavailable")
        return self._client.recover_transfer(action_id)
