"""Pure Wallet-facing human-readable journal rendering."""

from __future__ import annotations

from collections.abc import Iterable

from .model import JournalEvent


def render_event(event: JournalEvent) -> str:
    fields = event.public_fields
    details: list[str] = []
    for name in ("action_type", "amount_atomic", "asset", "network", "recipient"):
        if name in fields:
            details.append(f"{name}={fields[name]}")
    for name in ("contract", "selector", "calldata_hash", "transaction_hash"):
        if name in fields:
            details.append(f"{name}={fields[name]}")
    suffix = "" if not details else " | " + ", ".join(details)
    return f"{event.timestamp} | {event.description} | {event.code}{suffix}"


def render_journal(events: Iterable[JournalEvent]) -> tuple[str, ...]:
    return tuple(render_event(event) for event in reversed(tuple(events)))
