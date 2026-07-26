"""Strict non-authoritative Trusted recipients draft storage."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Mapping

from web3 import Web3

from holon_lending.action_profiles import ACTION_PROFILES_DIGEST
from holon_policy import LendingRule, Policy, RecipientRule, TransferRule, policy_digest

from .public_data import BASE_USDC, ETHEREUM_USDC, NETWORK_BY_ID
from .storage import StorageError, WalletPaths, atomic_write_json, read_json
from .transfer import (
    ETH_DECIMALS,
    USDC_DECIMALS,
    TransferPreflightError,
    format_atomic_amount,
    normalize_recipient,
    parse_transfer_amount,
)

DRAFT_SCHEMA_VERSION = "2"
MAX_DRAFT_BYTES = 64 * 1024
ENVELOPE_FIELDS = frozenset({
    "draft_schema_version", "policy", "policy_digest", "recipient_labels",
})
LABEL_FIELDS = frozenset({"network", "asset", "address", "label"})
ROUTE_ORDER = {
    ("ethereum", "eth"): 0,
    ("ethereum", "usdc"): 1,
    ("base", "eth"): 2,
    ("base", "usdc"): 3,
}
TOKEN_CONTRACTS = {ETHEREUM_USDC.lower(), BASE_USDC.lower()}


class TrustedDraftError(ValueError):
    """Draft input or persisted content is invalid."""


class TrustedDraftUnavailable(RuntimeError):
    """Persisted draft cannot be safely loaded or replaced implicitly."""


def trusted_draft_digest(value: Mapping[str, Any]) -> str:
    raw = json.dumps(
        dict(value), ensure_ascii=False, separators=(",", ":"), sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _decimals(asset: str) -> int:
    if asset == "eth":
        return ETH_DECIMALS
    if asset == "usdc":
        return USDC_DECIMALS
    raise TrustedDraftError("Unsupported asset")


def parse_cap(value: str, asset: str) -> str:
    try:
        atomic, _ = parse_transfer_amount(value, _decimals(asset))
    except TransferPreflightError as exc:
        raise TrustedDraftError("Enter a positive amount without rounding") from exc
    return str(atomic)


def parse_fee_cap(value: str) -> str:
    try:
        atomic, _ = parse_transfer_amount(value, ETH_DECIMALS)
    except TransferPreflightError as exc:
        raise TrustedDraftError("Enter a positive maximum fee in ETH") from exc
    return str(atomic)


def validate_label(value: str) -> str:
    normalized = value.strip() if isinstance(value, str) else ""
    if not 1 <= len(normalized) <= 40 or not normalized.isprintable():
        raise TrustedDraftError("Label must contain 1 to 40 printable characters")
    return normalized


def validate_draft_address(value: str, sender: str) -> str:
    try:
        normalized = normalize_recipient(value, sender)
    except TransferPreflightError as exc:
        raise TrustedDraftError("Enter a valid non-reserved EVM address") from exc
    if normalized.lower() in TOKEN_CONTRACTS:
        raise TrustedDraftError("Token contract addresses cannot be recipients")
    return normalized


@dataclass(frozen=True, slots=True)
class TrustedRecipientDraft:
    label: str
    address: str
    max_amount_atomic: str

    def display_amount(self, asset: str) -> str:
        return format_atomic_amount(int(self.max_amount_atomic), _decimals(asset))


@dataclass(frozen=True, slots=True)
class TrustedRouteDraft:
    network: str
    asset: str
    chain_id: int
    max_amount_atomic: str
    max_total_fee_wei: str
    recipients: tuple[TrustedRecipientDraft, ...]

    @property
    def key(self) -> tuple[str, str]:
        return self.network, self.asset

    def display_amount(self) -> str:
        return format_atomic_amount(int(self.max_amount_atomic), _decimals(self.asset))

    def display_fee(self) -> str:
        return format_atomic_amount(int(self.max_total_fee_wei), ETH_DECIMALS)


@dataclass(frozen=True, slots=True)
class TrustedPolicyDraft:
    routes: tuple[TrustedRouteDraft, ...] = ()
    lending_max_amount_atomic: str | None = None
    lending_max_total_fee_wei: str | None = None

    def canonical(self) -> "TrustedPolicyDraft":
        if any(route.key not in ROUTE_ORDER for route in self.routes):
            raise TrustedDraftError("Unsupported transfer route")
        routes = tuple(
            replace(
                route,
                recipients=tuple(sorted(
                    route.recipients, key=lambda item: item.address.lower(),
                )),
            )
            for route in sorted(self.routes, key=lambda item: ROUTE_ORDER[item.key])
        )
        return TrustedPolicyDraft(
            routes, self.lending_max_amount_atomic, self.lending_max_total_fee_wei,
        )

    def route(self, network: str, asset: str) -> TrustedRouteDraft | None:
        return next(
            (item for item in self.routes if item.key == (network, asset)), None,
        )

    def with_route(self, route: TrustedRouteDraft) -> "TrustedPolicyDraft":
        if route.key not in ROUTE_ORDER:
            raise TrustedDraftError("Unsupported transfer route")
        expected_chain = NETWORK_BY_ID[route.network].chain_id
        if route.chain_id != expected_chain:
            raise TrustedDraftError("Route chain ID does not match")
        if int(route.max_amount_atomic) <= 0 or int(route.max_total_fee_wei) <= 0:
            raise TrustedDraftError("Route limits must be positive")
        if any(
            int(recipient.max_amount_atomic) > int(route.max_amount_atomic)
            for recipient in route.recipients
        ):
            raise TrustedDraftError("Recipient limit exceeds route limit")
        addresses = {item.address.lower() for item in route.recipients}
        if len(addresses) != len(route.recipients) or len(addresses) > 64:
            raise TrustedDraftError("Recipient addresses must be unique")
        routes = tuple(item for item in self.routes if item.key != route.key) + (route,)
        if len(routes) > len(ROUTE_ORDER):
            raise TrustedDraftError("Too many transfer routes")
        return TrustedPolicyDraft(
            routes, self.lending_max_amount_atomic, self.lending_max_total_fee_wei,
        ).canonical()

    def without_route(self, network: str, asset: str) -> "TrustedPolicyDraft":
        return TrustedPolicyDraft(
            tuple(item for item in self.routes if item.key != (network, asset)),
            self.lending_max_amount_atomic, self.lending_max_total_fee_wei,
        ).canonical()

    def with_lending_limits(self, amount: str, fee: str) -> "TrustedPolicyDraft":
        amount_atomic = parse_cap(amount, "usdc")
        fee_wei = parse_fee_cap(fee)
        return TrustedPolicyDraft(self.routes, amount_atomic, fee_wei).canonical()

    def without_lending_limits(self) -> "TrustedPolicyDraft":
        return TrustedPolicyDraft(self.routes).canonical()

    def to_envelope(self) -> dict[str, Any]:
        canonical = self.canonical()
        if any(not route.recipients for route in canonical.routes):
            raise TrustedDraftError("Each route requires at least one recipient")
        for route in canonical.routes:
            if route.chain_id != NETWORK_BY_ID[route.network].chain_id:
                raise TrustedDraftError("Route chain ID does not match")
            if len(route.recipients) > 64:
                raise TrustedDraftError("Too many recipient rules")
            if len({item.address.lower() for item in route.recipients}) != len(route.recipients):
                raise TrustedDraftError("Recipient addresses must be unique")
            for recipient in route.recipients:
                validate_label(recipient.label)
                try:
                    checksum = Web3.to_checksum_address(recipient.address)
                except (TypeError, ValueError) as exc:
                    raise TrustedDraftError("Invalid recipient address") from exc
                if recipient.address != checksum or checksum.lower() in TOKEN_CONTRACTS:
                    raise TrustedDraftError("Recipient address is not canonical")
        rules = tuple(
            TransferRule(
                route.network,
                route.asset,
                route.chain_id,
                route.max_amount_atomic,
                route.max_total_fee_wei,
                tuple(
                    RecipientRule(item.address.lower(), item.max_amount_atomic)
                    for item in route.recipients
                ),
            )
            for route in canonical.routes
        )
        try:
            lending_rules = ()
            if self.lending_max_amount_atomic is not None or self.lending_max_total_fee_wei is not None:
                if self.lending_max_amount_atomic is None or self.lending_max_total_fee_wei is None:
                    raise TrustedDraftError("Lending limits are incomplete")
                lending_rules = (LendingRule(
                    "lending", "1", "aave-v3-base-usdc", "1", "base", "usdc",
                    8453, ("approve", "supply"), self.lending_max_amount_atomic,
                    self.lending_max_total_fee_wei, ACTION_PROFILES_DIGEST,
                ),)
            policy = Policy.from_dict(Policy(
                "3", "2", False, rules, False, lending_rules,
            ).to_dict())
        except ValueError as exc:
            raise TrustedDraftError("Invalid draft policy") from exc
        policy_value = policy.to_dict()
        labels = [
            {
                "network": route.network,
                "asset": route.asset,
                "address": recipient.address,
                "label": recipient.label,
            }
            for route in canonical.routes
            for recipient in route.recipients
        ]
        return {
            "draft_schema_version": DRAFT_SCHEMA_VERSION,
            "policy": policy_value,
            "policy_digest": policy_digest(policy_value),
            "recipient_labels": labels,
        }

    def to_policy(self) -> Policy:
        return Policy.from_dict(self.to_envelope()["policy"])

    @classmethod
    def from_envelope(cls, value: Mapping[str, Any]) -> "TrustedPolicyDraft":
        if not isinstance(value, Mapping) or set(value) != ENVELOPE_FIELDS:
            raise TrustedDraftError("Invalid draft fields")
        draft_version = value.get("draft_schema_version")
        if draft_version not in {"1", DRAFT_SCHEMA_VERSION}:
            raise TrustedDraftError("Unsupported draft version")
        raw_policy = value.get("policy")
        if not isinstance(raw_policy, Mapping):
            raise TrustedDraftError("Invalid draft policy")
        try:
            policy = Policy.from_dict(raw_policy)
        except ValueError as exc:
            raise TrustedDraftError("Invalid draft policy") from exc
        if policy.authority_enabled:
            raise TrustedDraftError("Draft authority must remain disabled")
        digest = value.get("policy_digest")
        if not isinstance(digest, str) or digest != policy_digest(policy.to_dict()):
            raise TrustedDraftError("Draft policy digest does not match")

        raw_labels = value.get("recipient_labels")
        if not isinstance(raw_labels, list):
            raise TrustedDraftError("Invalid recipient labels")
        labels: dict[tuple[str, str, str], tuple[str, str]] = {}
        for item in raw_labels:
            if not isinstance(item, Mapping) or set(item) != LABEL_FIELDS:
                raise TrustedDraftError("Invalid recipient label fields")
            network, asset, address = item.get("network"), item.get("asset"), item.get("address")
            if (network, asset) not in ROUTE_ORDER or not isinstance(address, str):
                raise TrustedDraftError("Invalid recipient label route")
            try:
                checksum = Web3.to_checksum_address(address)
            except (TypeError, ValueError) as exc:
                raise TrustedDraftError("Invalid recipient label address") from exc
            if address != checksum:
                raise TrustedDraftError("Recipient label address is not canonical")
            key = str(network), str(asset), checksum.lower()
            if key in labels:
                raise TrustedDraftError("Duplicate recipient label")
            labels[key] = checksum, validate_label(item.get("label"))

        routes: list[TrustedRouteDraft] = []
        for rule in policy.transfer_rules:
            key = (rule.network, rule.asset)
            if key not in ROUTE_ORDER or rule.chain_id != NETWORK_BY_ID[rule.network].chain_id:
                raise TrustedDraftError("Unsupported draft route")
            recipients: list[TrustedRecipientDraft] = []
            for recipient in rule.recipients:
                label_key = rule.network, rule.asset, recipient.address.lower()
                if label_key not in labels:
                    raise TrustedDraftError("Recipient label is missing")
                checksum, label = labels.pop(label_key)
                if checksum.lower() in TOKEN_CONTRACTS:
                    raise TrustedDraftError("Token contract recipient is forbidden")
                recipients.append(TrustedRecipientDraft(
                    label, checksum, recipient.max_amount_atomic,
                ))
            routes.append(TrustedRouteDraft(
                rule.network,
                rule.asset,
                rule.chain_id,
                rule.max_amount_atomic,
                rule.max_total_fee_wei,
                tuple(recipients),
            ))
        if labels:
            raise TrustedDraftError("Recipient label has no policy recipient")
        lending_amount = None
        lending_fee = None
        if policy.lending_rules:
            lending_amount = policy.lending_rules[0].max_amount_atomic
            lending_fee = policy.lending_rules[0].max_total_fee_wei
        draft = cls(tuple(routes), lending_amount, lending_fee).canonical()
        if draft_version == DRAFT_SCHEMA_VERSION and draft.to_envelope() != dict(value):
            raise TrustedDraftError("Draft is not canonical")
        return draft


class TrustedPolicyDraftStore:
    def __init__(self, paths: WalletPaths) -> None:
        self.path: Path = paths.authority_policy_draft
        self.legacy_path: Path = paths.transfer_policy_draft

    def load(self) -> TrustedPolicyDraft:
        path = self.path if self.path.exists() else self.legacy_path
        if not path.exists():
            return TrustedPolicyDraft()
        try:
            if path.stat().st_size > MAX_DRAFT_BYTES:
                raise TrustedDraftError("Draft is oversized")
            value = read_json(path)
            return TrustedPolicyDraft.from_envelope(value)
        except (OSError, StorageError, TrustedDraftError, TypeError, ValueError) as exc:
            raise TrustedDraftUnavailable("Trusted recipients draft is unavailable") from exc

    def save(self, draft: TrustedPolicyDraft) -> None:
        try:
            atomic_write_json(self.path, draft.to_envelope())
        except StorageError as exc:
            raise TrustedDraftUnavailable("Trusted recipients draft could not be saved") from exc
