from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from holon_wallet.authority import (
    ACTION_LIFETIME,
    ActionOutcome,
    ActionStateError,
    CriticalActionCoordinator,
    WalletAuthorityState,
)
from holon_wallet.model import ProfileSummary


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 7, 20, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def profile(profile_id: str = "profile-1") -> ProfileSummary:
    return ProfileSummary(
        profile_id,
        "Main Account",
        "0x1111111111111111111111111111111111111111",
        "mnemonic",
        "m/44'/60'/0'/0/0",
        "2026-07-20T12:00:00Z",
    )


def coordinator(clock: Clock | None = None) -> CriticalActionCoordinator:
    return CriticalActionCoordinator(clock or Clock(), lambda: "act-fixed")


def test_prepare_is_immutable_complete_and_deterministic() -> None:
    item = coordinator()
    assert item.state is WalletAuthorityState.LOCKED

    action = item.prepare(profile())

    assert item.state is WalletAuthorityState.PREPARED
    assert action.network == "Base"
    assert action.chain_id == 8453
    assert action.token == "USDC"
    assert action.amount_atomic == 1_000_000
    assert action.simulation is True
    assert action.expires_at - action.created_at == ACTION_LIFETIME
    assert action.digest == replace(action).digest
    with pytest.raises(AttributeError):
        action.amount_atomic = 2_000_000  # type: ignore[misc]


def test_correct_action_is_consumed_once_and_replay_is_refused() -> None:
    item = coordinator()
    action = item.prepare(profile())
    consumed: list[str] = []

    assert item.preflight(action.action_id, action.digest) is None
    result = item.authorize_and_consume(
        action.action_id, action.digest, lambda current: consumed.append(current.action_id),
    )

    assert result is ActionOutcome.AUTHORIZED
    assert consumed == [action.action_id]
    assert item.state is WalletAuthorityState.LOCKED
    assert item.current is None
    assert item.authorize_and_consume(
        action.action_id, action.digest, lambda current: consumed.append(current.action_id),
    ) is ActionOutcome.REPLAYED
    assert consumed == [action.action_id]


def test_second_active_action_and_terminal_id_reuse_are_refused() -> None:
    item = coordinator()
    action = item.prepare(profile())
    with pytest.raises(ActionStateError):
        item.prepare(profile("profile-2"))
    item.cancel()
    with pytest.raises(ActionStateError):
        item.prepare(profile())
    assert action.action_id == "act-fixed"


@pytest.mark.parametrize(
    ("terminal", "expected"),
    [
        ("reject", ActionOutcome.REJECTED),
        ("cancel", ActionOutcome.CANCELLED),
        ("fail", ActionOutcome.FAILED),
    ],
)
def test_terminal_paths_return_to_locked(terminal: str, expected: ActionOutcome) -> None:
    item = coordinator()
    item.prepare(profile())

    assert getattr(item, terminal)() is expected
    assert item.state is WalletAuthorityState.LOCKED
    assert item.current is None


def test_wrong_password_is_terminal() -> None:
    item = coordinator()
    action = item.prepare(profile())

    assert item.authentication_failed(action.action_id) is ActionOutcome.AUTHENTICATION_FAILED
    assert item.state is WalletAuthorityState.LOCKED
    assert item.preflight(action.action_id, action.digest) is ActionOutcome.REPLAYED


def test_expiry_and_mutation_are_terminal() -> None:
    clock = Clock()
    expired = coordinator(clock)
    action = expired.prepare(profile())
    clock.now += ACTION_LIFETIME
    assert expired.preflight(action.action_id, action.digest) is ActionOutcome.EXPIRED
    assert expired.state is WalletAuthorityState.LOCKED

    mutated = coordinator()
    original = mutated.prepare(profile())
    changed = replace(original, amount_atomic=2_000_000)
    assert changed.digest != original.digest
    assert mutated.preflight(original.action_id, changed.digest) is ActionOutcome.INVALIDATED
    assert mutated.state is WalletAuthorityState.LOCKED


def test_profile_change_consumer_failure_close_and_restart_lock_authority() -> None:
    changed = coordinator()
    changed.prepare(profile())
    assert not changed.profile_changed("profile-1")
    assert changed.profile_changed("profile-2")
    assert changed.state is WalletAuthorityState.LOCKED

    failed = coordinator()
    action = failed.prepare(profile())

    def broken_consumer(_action) -> None:
        raise RuntimeError("safe failure")

    assert failed.authorize_and_consume(
        action.action_id, action.digest, broken_consumer,
    ) is ActionOutcome.FAILED
    assert failed.state is WalletAuthorityState.LOCKED

    closed = coordinator()
    closed.prepare(profile())
    closed.close()
    assert closed.state is WalletAuthorityState.LOCKED
    assert coordinator().state is WalletAuthorityState.LOCKED
