"""Shared public Lending portfolio, tracked earnings, and observations."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from web3 import Web3

from .runtime import (
    ASSET, COMPARE_CACHE_SECONDS, NETWORK, PINNED_CONTRACTS, PROTOCOLS, LendingReader,
)

ANALYTICS_SCHEMA_VERSION = 2
LEGACY_ANALYTICS_SCHEMA_VERSION = 1
MAX_ACCOUNTS = 20
MAX_DAILY_HISTORY = 31
MAX_PROCESSED_ACTIONS = 500
PROTOCOL_IDS = tuple(item[0] for item in PROTOCOLS)
PROTOCOL_LABELS = {item[0]: item[2] for item in PROTOCOLS}
HISTORY_PERIODS = frozenset({"none", "7d", "30d", "all"})


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _day_bounds(timestamp: str) -> tuple[str, str]:
    observed = _parse_timestamp(timestamp).astimezone(UTC)
    start = observed.replace(hour=0, minute=0, second=0, microsecond=0)
    return _format_timestamp(start), _format_timestamp(start + timedelta(days=1, seconds=-1))


def _month_bounds(timestamp: str) -> tuple[str, str]:
    observed = _parse_timestamp(timestamp).astimezone(UTC)
    start = observed.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    following = (
        start.replace(year=start.year + 1, month=1)
        if start.month == 12 else start.replace(month=start.month + 1)
    )
    return _format_timestamp(start), _format_timestamp(following - timedelta(seconds=1))


def _decimal_text(value: Decimal) -> str:
    text = format(value, "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _merge_into_period(
    target: list[dict[str, Any]], source: Mapping[str, Any],
    period_start: str, period_end: str,
) -> None:
    existing = next(
        (item for item in target if item.get("period_start") == period_start), None,
    )
    if "rates" in source:
        source_totals = {
            protocol: (str(source["rates"].get(protocol))
                       if source["rates"].get(protocol) is not None else None)
            for protocol in PROTOCOL_IDS
        }
        source_counts = {
            protocol: 1 if source_totals[protocol] is not None else 0
            for protocol in PROTOCOL_IDS
        }
        source_count = 1
    else:
        source_totals = dict(source["rate_totals"])
        source_counts = dict(source["rate_counts"])
        source_count = int(source["observation_count"])
    if existing is None:
        target.append({
            "period_start": period_start,
            "period_end": period_end,
            "observed_at": str(source["observed_at"]),
            "total_position_atomic": source.get("total_position_atomic"),
            "tracked_earnings_atomic": source.get("tracked_earnings_atomic"),
            "rate_totals": source_totals,
            "rate_counts": source_counts,
            "observation_count": source_count,
        })
        target.sort(key=lambda item: str(item["period_start"]))
        return
    for protocol in PROTOCOL_IDS:
        incoming = source_totals[protocol]
        if incoming is None:
            continue
        current = existing["rate_totals"].get(protocol)
        existing["rate_totals"][protocol] = _decimal_text(
            Decimal(str(current or "0")) + Decimal(incoming),
        )
        existing["rate_counts"][protocol] += source_counts[protocol]
    existing["observation_count"] += source_count
    if str(source["observed_at"]) >= str(existing["observed_at"]):
        existing["observed_at"] = str(source["observed_at"])
        existing["total_position_atomic"] = source.get("total_position_atomic")
        existing["tracked_earnings_atomic"] = source.get("tracked_earnings_atomic")


def _compact_daily(
    daily: list[dict[str, Any]], monthly: list[dict[str, Any]], reference: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    reference_day = _parse_timestamp(reference).astimezone(UTC).date()
    cutoff = reference_day - timedelta(days=30)
    retained: list[dict[str, Any]] = []
    compacted: list[dict[str, Any]] = []
    for item in sorted(daily, key=lambda value: str(value["period_start"])):
        day = _parse_timestamp(str(item["period_start"])).astimezone(UTC).date()
        (compacted if day < cutoff else retained).append(item)
    while len(retained) > MAX_DAILY_HISTORY:
        compacted.append(retained.pop(0))
    for item in compacted:
        start, end = _month_bounds(str(item["observed_at"]))
        _merge_into_period(monthly, item, start, end)
    return retained, sorted(monthly, key=lambda value: str(value["period_start"]))


class LendingAnalyticsMigrationError(ValueError):
    """Legacy public analytics cannot be migrated without data loss."""


class LendingAnalyticsStore:
    """Best-effort atomic persistence for compact public Lending analytics."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()
        self._write_blocked = False

    def load(self, address: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                envelope = self._load_envelope()
            except LendingAnalyticsMigrationError:
                self._write_blocked = True
                return None
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                self._write_blocked = self.path.exists()
                return None
            value = next(
                (
                    item for item in envelope["accounts"]
                    if item.get("address") == address
                ),
                None,
            )
            if value is None:
                return None
            if not self._valid_account_state(value):
                self._write_blocked = True
                return None
            return deepcopy(value)

    def save(self, value: Mapping[str, Any]) -> bool:
        address = value.get("address")
        if not isinstance(address, str) or self._write_blocked:
            return False
        with self._lock:
            try:
                envelope = self._load_envelope()
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                envelope = {"schema_version": ANALYTICS_SCHEMA_VERSION, "accounts": []}
            retained = [
                item for item in envelope["accounts"]
                if item.get("address") != address
            ][-(MAX_ACCOUNTS - 1):]
            retained.append(deepcopy(dict(value)))
            self._atomic_write({
                "schema_version": ANALYTICS_SCHEMA_VERSION,
                "accounts": retained,
            })
            return True

    def _load_envelope(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": ANALYTICS_SCHEMA_VERSION, "accounts": []}
        with self.path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
        if (
            isinstance(value, dict)
            and value.get("schema_version") == LEGACY_ANALYTICS_SCHEMA_VERSION
        ):
            try:
                migrated = self._migrate_envelope(value)
            except (TypeError, ValueError) as error:
                raise LendingAnalyticsMigrationError(
                    "Legacy Lending analytics migration failed"
                ) from error
            self._atomic_write(migrated)
            self._write_blocked = False
            return migrated
        if (
            not isinstance(value, dict)
            or set(value) != {"schema_version", "accounts"}
            or value["schema_version"] != ANALYTICS_SCHEMA_VERSION
            or not isinstance(value["accounts"], list)
            or len(value["accounts"]) > MAX_ACCOUNTS
            or any(not isinstance(item, dict) for item in value["accounts"])
        ):
            raise ValueError("Lending analytics envelope is invalid")
        return value

    def _atomic_write(self, value: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent,
        )
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
                descriptor = -1
                json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            temporary.unlink(missing_ok=True)

    @staticmethod
    def _valid_account_state(value: Mapping[str, Any]) -> bool:
        current = value.get("current")
        protocols = current.get("protocols") if isinstance(current, Mapping) else None
        daily = value.get("daily_history")
        monthly = value.get("monthly_history")
        baselines = value.get("baselines")
        if (
            not isinstance(value.get("address"), str)
            or not isinstance(value.get("label"), str)
            or not LendingAnalyticsStore._valid_timestamp(value.get("saved_at"))
            or not isinstance(protocols, list)
            or len(protocols) != len(PROTOCOL_IDS)
            or {item.get("protocol") for item in protocols if isinstance(item, Mapping)}
            != set(PROTOCOL_IDS)
            or any(
                not LendingAnalyticsStore._valid_protocol(item)
                for item in protocols
            )
            or not isinstance(daily, list)
            or len(daily) > MAX_DAILY_HISTORY
            or any(not LendingAnalyticsStore._valid_bucket(item) for item in daily)
            or not isinstance(monthly, list)
            or any(not LendingAnalyticsStore._valid_bucket(item) for item in monthly)
            or not isinstance(baselines, Mapping)
            or any(
                protocol not in PROTOCOL_IDS
                or not LendingAnalyticsStore._valid_baseline(baseline)
                for protocol, baseline in baselines.items()
            )
        ):
            return False
        return True

    @staticmethod
    def _valid_protocol(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        required = {
            "protocol", "position_atomic", "tracked_earnings_atomic", "data_state",
            "caveats", "confirmed_total_annual_percent",
        }
        position = value.get("position_atomic")
        earnings = value.get("tracked_earnings_atomic")
        rate = value.get("confirmed_total_annual_percent")
        return bool(
            required <= set(value)
            and value.get("data_state") in {"LIVE", "STALE", "CACHED", "UNAVAILABLE"}
            and isinstance(value.get("caveats"), list)
            and (position is None or isinstance(position, str) and position.isdecimal())
            and (
                earnings is None
                or isinstance(earnings, str) and earnings.lstrip("-").isdecimal()
            )
            and (rate is None or isinstance(rate, str))
        )

    @staticmethod
    def _valid_timestamp(value: object) -> bool:
        if not isinstance(value, str):
            return False
        try:
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return False
        return True

    @staticmethod
    def _valid_bucket(value: object) -> bool:
        if (
            not isinstance(value, Mapping)
            or set(value) != {
                "period_start", "period_end", "observed_at",
                "total_position_atomic", "tracked_earnings_atomic",
                "rate_totals", "rate_counts", "observation_count",
            }
            or not all(
                LendingAnalyticsStore._valid_timestamp(value.get(name))
                for name in ("period_start", "period_end", "observed_at")
            )
            or not LendingAnalyticsStore._optional_atomic(
                value.get("total_position_atomic"), signed=False,
            )
            or not LendingAnalyticsStore._optional_atomic(
                value.get("tracked_earnings_atomic"), signed=True,
            )
        ):
            return False
        totals = value.get("rate_totals")
        counts = value.get("rate_counts")
        return bool(
            isinstance(totals, Mapping) and set(totals) == set(PROTOCOL_IDS)
            and all(item is None or LendingAnalyticsStore._decimal(item) for item in totals.values())
            and isinstance(counts, Mapping) and set(counts) == set(PROTOCOL_IDS)
            and all(type(item) is int and item >= 0 for item in counts.values())
            and all((totals[key] is None) == (counts[key] == 0) for key in PROTOCOL_IDS)
            and type(value.get("observation_count")) is int
            and value["observation_count"] > 0
        )

    @staticmethod
    def _valid_baseline(value: object) -> bool:
        if not isinstance(value, Mapping):
            return False
        processed = value.get("processed_action_ids")
        return bool(
            LendingAnalyticsStore._valid_timestamp(value.get("started_at"))
            and isinstance(value.get("position_atomic"), str)
            and value["position_atomic"].isdecimal()
            and isinstance(value.get("net_contributions_atomic"), str)
            and value["net_contributions_atomic"].lstrip("-").isdecimal()
            and isinstance(processed, list)
            and len(processed) <= MAX_PROCESSED_ACTIONS
            and all(isinstance(item, str) for item in processed)
            and type(value.get("history_complete")) is bool
        )

    @staticmethod
    def _optional_atomic(value: object, *, signed: bool) -> bool:
        return bool(
            value is None
            or isinstance(value, str)
            and (value.lstrip("-").isdecimal() if signed else value.isdecimal())
        )

    @staticmethod
    def _decimal(value: object) -> bool:
        try:
            parsed = Decimal(str(value))
        except Exception:
            return False
        return parsed.is_finite() and parsed >= 0

    def _migrate_envelope(self, value: Mapping[str, Any]) -> dict[str, Any]:
        accounts = value.get("accounts")
        if (
            set(value) != {"schema_version", "accounts"}
            or not isinstance(accounts, list)
            or len(accounts) > MAX_ACCOUNTS
            or any(not isinstance(item, Mapping) for item in accounts)
        ):
            raise ValueError("Legacy Lending analytics envelope is invalid")
        migrated = {
            "schema_version": ANALYTICS_SCHEMA_VERSION,
            "accounts": [self._migrate_account(item) for item in accounts],
        }
        if any(not self._valid_account_state(item) for item in migrated["accounts"]):
            raise ValueError("Migrated Lending analytics state is invalid")
        return migrated

    def _migrate_account(self, value: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "address", "label", "saved_at", "current", "baselines", "observations",
        }
        if set(value) != required or not isinstance(value.get("observations"), list):
            raise ValueError("Legacy Lending analytics Account is invalid")
        reference = str(value["saved_at"])
        if not self._valid_timestamp(reference):
            raise ValueError("Legacy Lending analytics timestamp is invalid")
        daily: list[dict[str, Any]] = []
        monthly: list[dict[str, Any]] = []
        for observation in sorted(
            value["observations"], key=lambda item: str(item.get("observed_at", ""))
            if isinstance(item, Mapping) else "",
        ):
            normalized = self._legacy_observation(observation)
            day_start, day_end = _day_bounds(normalized["observed_at"])
            _merge_into_period(daily, normalized, day_start, day_end)
        daily, monthly = _compact_daily(daily, monthly, reference)
        return {
            "address": value["address"],
            "label": value["label"],
            "saved_at": reference,
            "current": deepcopy(value["current"]),
            "baselines": deepcopy(value["baselines"]),
            "daily_history": daily,
            "monthly_history": monthly,
        }

    def _legacy_observation(self, value: object) -> dict[str, Any]:
        if not isinstance(value, Mapping) or set(value) != {
            "observed_at", "total_position_atomic", "tracked_earnings_atomic", "rates",
        }:
            raise ValueError("Legacy Lending observation is invalid")
        observed = value.get("observed_at")
        rates = value.get("rates")
        if (
            not self._valid_timestamp(observed)
            or not self._optional_atomic(value.get("total_position_atomic"), signed=False)
            or not self._optional_atomic(value.get("tracked_earnings_atomic"), signed=True)
            or not isinstance(rates, Mapping)
            or set(rates) != set(PROTOCOL_IDS)
            or any(item is not None and not self._decimal(item) for item in rates.values())
        ):
            raise ValueError("Legacy Lending observation is invalid")
        return {
            "observed_at": observed,
            "total_position_atomic": value.get("total_position_atomic"),
            "tracked_earnings_atomic": value.get("tracked_earnings_atomic"),
            "rates": dict(rates),
        }


class LendingPortfolioService:
    """Combines current reads with bounded, non-authoritative local analytics."""

    def __init__(
        self,
        reader: LendingReader,
        store: LendingAnalyticsStore,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._reader = reader
        self._store = store
        self._clock = clock or time.time
        self._cache: dict[str, tuple[int, dict[str, Any]]] = {}
        self._lock = threading.RLock()

    def read(
        self,
        account: Mapping[str, str] | None,
        operations: Sequence[Mapping[str, object]] | None,
        *,
        force_refresh: bool = False,
        history_period: str = "none",
        history_limit: int = 120,
    ) -> dict[str, Any]:
        if history_period not in HISTORY_PERIODS:
            raise ValueError("Unsupported Lending history period")
        if type(history_limit) is not int or not 0 <= history_limit <= 120:
            raise ValueError("Unsupported Lending history limit")
        if not self._valid_account(account):
            return self.unavailable(account, history_period)
        assert account is not None
        address = account["address"]
        now = int(self._clock())
        with self._lock:
            cached = self._cache.get(address)
            if not force_refresh and cached is not None and now - cached[0] <= COMPARE_CACHE_SECONDS:
                response = deepcopy(cached[1])
                response["delivery"] = self._delivery(cached[0], now, False, "MEMORY_CACHE")
                response["history"] = self._history(
                    address, history_period, now, history_limit,
                )
                return response

        markets, positions = self._read_current(account, force_refresh)
        stored = self._store.load(address)
        protocols = self._combine(markets, positions, stored)
        baselines = self._baselines(stored, protocols, operations, now)
        self._apply_earnings(protocols, baselines)
        response = self._response(account, markets, protocols, now, force_refresh)
        account_state = self._account_state(account, response, baselines, stored, now)
        try:
            history_available = self._store.save(account_state)
        except OSError:
            history_available = False
        with self._lock:
            self._cache[address] = (now, deepcopy(response))
        response["history"] = self._history_from_state(
            account_state if history_available else None,
            history_period,
            now,
            history_limit,
        )
        return response

    def cached(
        self,
        account: Mapping[str, str] | None,
        history_period: str = "none",
        history_limit: int = 120,
    ) -> dict[str, Any]:
        if history_period not in HISTORY_PERIODS or not self._valid_account(account):
            return self.unavailable(account, history_period)
        assert account is not None
        now = int(self._clock())
        stored = self._store.load(account["address"])
        current = stored.get("current") if stored else None
        protocols = deepcopy(current.get("protocols")) if isinstance(current, Mapping) else None
        if not isinstance(protocols, list) or len(protocols) != 3:
            return self.unavailable(account, history_period)
        for item in protocols:
            if item.get("data_state") != "UNAVAILABLE":
                item["data_state"] = "CACHED"
                item["caveats"] = list(dict.fromkeys(
                    list(item.get("caveats", [])) + ["USING_CACHED_DATA"]
                ))
        response = self._response(
            account,
            {"recommendation": deepcopy(current.get("recommendation"))},
            protocols,
            now,
            False,
        )
        saved_at = stored.get("saved_at") if stored else None
        try:
            fetched = int(datetime.fromisoformat(
                str(saved_at).replace("Z", "+00:00")
            ).timestamp())
        except (TypeError, ValueError):
            fetched = 0
        response["delivery"] = self._delivery(
            fetched, now, False, "PERSISTED_FALLBACK",
        )
        response["history"] = self._history_from_state(
            stored, history_period, now, history_limit,
        )
        return response

    @staticmethod
    def _valid_account(account: Mapping[str, str] | None) -> bool:
        return bool(
            isinstance(account, Mapping)
            and isinstance(account.get("label"), str)
            and account.get("label")
            and isinstance(account.get("address"), str)
            and Web3.is_checksum_address(account["address"])
        )

    def _read_current(
        self, account: Mapping[str, str], force_refresh: bool,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="holon-lending-portfolio") as pool:
            market_future = pool.submit(self._reader.compare, force_refresh)
            position_future = pool.submit(self._reader.positions, account)
            try:
                markets = market_future.result()
            except Exception:
                markets = {"markets": [], "recommendation": None, "status": "DEGRADED"}
            try:
                positions = position_future.result()
            except Exception:
                positions = {"positions": [], "status": "DEGRADED"}
        return markets, positions

    def _combine(
        self,
        market_payload: Mapping[str, Any],
        position_payload: Mapping[str, Any],
        stored: Mapping[str, Any] | None,
    ) -> list[dict[str, Any]]:
        market_by_id = self._by_protocol(market_payload.get("markets"))
        position_by_id = self._by_protocol(position_payload.get("positions"))
        stored_by_id = self._by_protocol(
            stored.get("current", {}).get("protocols") if stored else None,
        )
        result: list[dict[str, Any]] = []
        for protocol_id, market_id, label in PROTOCOLS:
            market = market_by_id.get(protocol_id)
            position = position_by_id.get(protocol_id)
            previous = stored_by_id.get(protocol_id)
            market_live = self._usable(market)
            position_live = self._usable(position)
            amount = position.get("amount_atomic") if position_live else None
            amount_source = position if amount is not None else None
            if amount is None and previous is not None:
                amount = previous.get("position_atomic")
                if amount is not None:
                    amount_source = previous
            rate = market.get("confirmed_total_annual_percent") if market_live else None
            rate_source = market if rate is not None else None
            if rate is None and previous is not None:
                rate = previous.get("confirmed_total_annual_percent")
                if rate is not None:
                    rate_source = previous
            data_state = (
                "LIVE" if market_live and position_live
                and market["freshness"]["state"] == "LIVE"
                and position["freshness"]["state"] == "LIVE"
                else "STALE" if market_live and position_live
                else "CACHED" if previous is not None and (amount is not None or rate is not None)
                else "UNAVAILABLE"
            )
            source = rate_source or {}
            observed = self._observed_at(amount_source, rate_source)
            caveats = list(dict.fromkeys(
                list((market or {}).get("caveats", []))
                + list((position or {}).get("caveats", []))
                + (["USING_CACHED_DATA"] if data_state == "CACHED" else [])
            ))
            result.append({
                "protocol": protocol_id,
                "market_id": market_id,
                "display_name": label,
                "contract_address": (
                    (position or {}).get("contract_address")
                    or (market or {}).get("contract_address")
                    or (previous or {}).get("contract_address")
                ),
                "position_atomic": str(amount) if amount is not None else None,
                "display_position": self._display_amount(amount),
                "base_yield": deepcopy(source.get("base_yield")),
                "incentives": deepcopy(source.get("incentives")),
                "confirmed_total_annual_percent": str(rate) if rate is not None else None,
                "total_completeness": source.get("total_completeness", "UNAVAILABLE"),
                "tracked_earnings_atomic": None,
                "display_tracked_earnings": None,
                "earnings_status": "NOT_ENOUGH_HISTORY",
                "tracked_since": None,
                "data_state": data_state,
                "observed_at": observed,
                "caveats": caveats,
            })
        return result

    @staticmethod
    def _by_protocol(value: object) -> dict[str, Mapping[str, Any]]:
        if not isinstance(value, list):
            return {}
        return {
            str(item["protocol"]): item for item in value
            if isinstance(item, Mapping) and item.get("protocol") in PROTOCOL_IDS
        }

    @staticmethod
    def _usable(value: Mapping[str, Any] | None) -> bool:
        freshness = value.get("freshness") if value else None
        return bool(
            isinstance(freshness, Mapping)
            and freshness.get("state") in {"LIVE", "STALE"}
        )

    @staticmethod
    def _observed_at(*values: Mapping[str, Any] | None) -> str | None:
        timestamps: list[str] = []
        for value in values:
            if not value:
                continue
            freshness = value.get("freshness")
            observed = (
                freshness.get("observed_at")
                if isinstance(freshness, Mapping) else value.get("observed_at")
            )
            if isinstance(observed, str):
                timestamps.append(observed)
        return min(timestamps) if timestamps else None

    def _baselines(
        self,
        stored: Mapping[str, Any] | None,
        protocols: Sequence[Mapping[str, Any]],
        operations: Sequence[Mapping[str, object]] | None,
        now: int,
    ) -> dict[str, dict[str, Any]]:
        existing = deepcopy(stored.get("baselines", {})) if stored else {}
        result: dict[str, dict[str, Any]] = {}
        timestamp = self._timestamp(now)
        for item in protocols:
            protocol = str(item["protocol"])
            baseline = existing.get(protocol)
            if not isinstance(baseline, dict) and item["data_state"] in {"LIVE", "STALE"}:
                baseline = {
                    "started_at": timestamp,
                    "position_atomic": item["position_atomic"],
                    "net_contributions_atomic": "0",
                    "processed_action_ids": [],
                    "history_complete": operations is not None,
                }
            if not isinstance(baseline, dict):
                continue
            processed = set(baseline.get("processed_action_ids", []))
            net = int(str(baseline.get("net_contributions_atomic", "0")))
            complete = bool(baseline.get("history_complete", False)) and operations is not None
            if operations is not None:
                for operation in operations:
                    action_id = operation.get("action_id")
                    if (
                        operation.get("protocol") != protocol
                        or not isinstance(action_id, str)
                        or str(operation.get("updated_at", "")) < str(baseline["started_at"])
                    ):
                        continue
                    amount = operation.get("amount_atomic")
                    valid_cashflow = (
                        operation.get("verified") is True
                        and isinstance(amount, str)
                        and amount.isdecimal()
                    )
                    if action_id in processed:
                        # Re-evaluate retained actions. Older versions accepted
                        # zero-delta all-withdraws and could show false losses.
                        if not valid_cashflow:
                            complete = False
                        continue
                    if not valid_cashflow:
                        complete = False
                        processed.add(action_id)
                        continue
                    delta = int(amount)
                    net += delta if operation.get("direction") == "supply" else -delta
                    processed.add(action_id)
            baseline["net_contributions_atomic"] = str(net)
            current_action_ids = [
                str(operation["action_id"]) for operation in (operations or ())
                if operation.get("protocol") == protocol
                and isinstance(operation.get("action_id"), str)
            ]
            baseline["processed_action_ids"] = [
                action_id for action_id in current_action_ids if action_id in processed
            ][-MAX_PROCESSED_ACTIONS:]
            baseline["history_complete"] = complete
            result[protocol] = baseline
        return result

    @staticmethod
    def _apply_earnings(
        protocols: Sequence[dict[str, Any]], baselines: Mapping[str, Mapping[str, Any]],
    ) -> None:
        for item in protocols:
            baseline = baselines.get(item["protocol"])
            current = item.get("position_atomic")
            if (
                baseline is None or baseline.get("history_complete") is not True
                or not isinstance(current, str) or not current.isdecimal()
            ):
                continue
            earned = (
                int(current) - int(str(baseline["position_atomic"]))
                - int(str(baseline["net_contributions_atomic"]))
            )
            item["tracked_earnings_atomic"] = str(earned)
            item["display_tracked_earnings"] = LendingPortfolioService._display_signed(earned)
            item["earnings_status"] = "AVAILABLE"
            item["tracked_since"] = baseline["started_at"]

    def _response(
        self,
        account: Mapping[str, str],
        market_payload: Mapping[str, Any],
        protocols: list[dict[str, Any]],
        now: int,
        force_refresh: bool,
    ) -> dict[str, Any]:
        usable = [item for item in protocols if item["data_state"] != "UNAVAILABLE"]
        status = (
            "READY" if all(item["data_state"] == "LIVE" for item in protocols)
            else "PARTIAL" if usable else "DEGRADED"
        )
        amounts = [item["position_atomic"] for item in protocols]
        total = str(sum(int(item) for item in amounts)) if all(
            isinstance(item, str) and item.isdecimal() for item in amounts
        ) else None
        earnings = [item["tracked_earnings_atomic"] for item in protocols]
        total_earnings = str(sum(int(item) for item in earnings)) if all(
            isinstance(item, str) and item.lstrip("-").isdecimal() for item in earnings
        ) else None
        weighted, completeness = self._weighted_yield(protocols, total)
        codes = {
            "READY": ("LENDING_PORTFOLIO_READY", "Lending portfolio is available."),
            "PARTIAL": ("LENDING_PORTFOLIO_PARTIAL", "Some Lending portfolio data is unavailable or cached."),
            "DEGRADED": ("LENDING_PORTFOLIO_UNAVAILABLE", "Lending portfolio is unavailable."),
        }
        code, message = codes[status]
        return {
            "status": status,
            "authority_available": False,
            "account": dict(account),
            "network": dict(NETWORK),
            "asset": dict(ASSET),
            "summary": {
                "total_position_atomic": total,
                "display_total_position": self._display_amount(total),
                "tracked_earnings_atomic": total_earnings,
                "display_tracked_earnings": self._display_signed_value(total_earnings),
                "earnings_status": "AVAILABLE" if total_earnings is not None else "NOT_ENOUGH_HISTORY",
                "weighted_confirmed_annual_percent": weighted,
                "yield_completeness": completeness,
            },
            "protocols": protocols,
            "recommendation": deepcopy(market_payload.get("recommendation")),
            "delivery": self._delivery(now, now, force_refresh, "LIVE_READ"),
            "history": self._empty_history("none"),
            "code": code,
            "message": message,
        }

    @staticmethod
    def _weighted_yield(
        protocols: Sequence[Mapping[str, Any]], total: str | None,
    ) -> tuple[str | None, str]:
        if total is None:
            return None, "PARTIAL"
        total_value = int(total)
        if total_value == 0:
            return None, "EMPTY"
        numerator = Decimal(0)
        for item in protocols:
            amount = int(str(item["position_atomic"]))
            rate = item.get("confirmed_total_annual_percent")
            if amount and not isinstance(rate, str):
                return None, "PARTIAL"
            if amount:
                numerator += Decimal(amount) * Decimal(rate)
        value = (numerator / Decimal(total_value)).quantize(
            Decimal("0.000001"), rounding=ROUND_HALF_UP,
        )
        return format(value, "f").rstrip("0").rstrip(".") or "0", "COMPLETE"

    def _account_state(
        self,
        account: Mapping[str, str],
        response: Mapping[str, Any],
        baselines: Mapping[str, Any],
        stored: Mapping[str, Any] | None,
        now: int,
    ) -> dict[str, Any]:
        daily = deepcopy(stored.get("daily_history", [])) if stored else []
        monthly = deepcopy(stored.get("monthly_history", [])) if stored else []
        current = {
            "protocols": deepcopy(response["protocols"]),
            "summary": deepcopy(response["summary"]),
            "recommendation": deepcopy(response["recommendation"]),
        }
        observation = {
            "observed_at": self._timestamp(now),
            "total_position_atomic": response["summary"]["total_position_atomic"],
            "tracked_earnings_atomic": response["summary"]["tracked_earnings_atomic"],
            "rates": {
                item["protocol"]: (
                    item["confirmed_total_annual_percent"]
                    if item["data_state"] in {"LIVE", "STALE"} else None
                )
                for item in response["protocols"]
            },
        }
        day_start, day_end = _day_bounds(observation["observed_at"])
        _merge_into_period(daily, observation, day_start, day_end)
        daily, monthly = _compact_daily(daily, monthly, observation["observed_at"])
        return {
            "address": account["address"],
            "label": account["label"],
            "saved_at": self._timestamp(now),
            "current": current,
            "baselines": deepcopy(dict(baselines)),
            "daily_history": daily,
            "monthly_history": monthly,
        }

    def _history(
        self, address: str, period: str, now: int, limit: int,
    ) -> dict[str, Any]:
        stored = self._store.load(address)
        return self._history_from_state(stored, period, now, limit)

    def _history_from_state(
        self, stored: Mapping[str, Any] | None, period: str, now: int, limit: int,
    ) -> dict[str, Any]:
        if period == "none":
            return self._empty_history(period)
        if stored is None:
            return self._empty_history(period)
        daily = deepcopy(list(stored.get("daily_history", [])))
        monthly = deepcopy(list(stored.get("monthly_history", [])))
        today = datetime.fromtimestamp(now, UTC).date()
        buckets: list[dict[str, Any]]
        granularity: str
        if period == "7d":
            cutoff = today - timedelta(days=6)
            buckets = [
                item for item in daily
                if _parse_timestamp(str(item["period_start"])).date() >= cutoff
            ]
            granularity = "day"
        elif period == "30d":
            cutoff = today - timedelta(days=29)
            selected = [
                item for item in daily
                if _parse_timestamp(str(item["period_start"])).date() >= cutoff
            ]
            buckets = self._ten_day_buckets(selected, cutoff, today)
            granularity = "ten_day"
        else:
            source = sorted(monthly + daily, key=lambda item: str(item["period_start"]))
            if not source:
                return self._empty_history(period)
            first_day = _parse_timestamp(str(source[0]["period_start"])).date()
            age = (today - first_day).days + 1
            if age <= 7:
                buckets, granularity = daily, "day"
            elif age <= 30:
                buckets = self._ten_day_buckets(daily, first_day, today)
                granularity = "ten_day"
            else:
                buckets = deepcopy(monthly)
                for item in daily:
                    start, end = _month_bounds(str(item["observed_at"]))
                    _merge_into_period(buckets, item, start, end)
                granularity = "month"
        buckets.sort(key=lambda item: str(item["period_start"]))
        if limit == 0:
            buckets = []
        elif len(buckets) > limit:
            indexes = {
                round(index * (len(buckets) - 1) / (limit - 1))
                for index in range(limit)
            } if limit > 1 else {len(buckets) - 1}
            buckets = [item for index, item in enumerate(buckets) if index in indexes]
        return {
            "period": period,
            "granularity": granularity if buckets else "none",
            "period_start": buckets[0]["period_start"] if buckets else None,
            "period_end": buckets[-1]["period_end"] if buckets else None,
            "points": [self._bucket_point(item) for item in buckets],
        }

    @staticmethod
    def _ten_day_buckets(
        daily: Sequence[Mapping[str, Any]], start_day, end_day,
    ) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for item in daily:
            day = _parse_timestamp(str(item["period_start"])).date()
            if day < start_day or day > end_day:
                continue
            index = (day - start_day).days // 10
            group_start = datetime.combine(
                start_day + timedelta(days=index * 10), datetime.min.time(), UTC,
            )
            group_end_day = min(start_day + timedelta(days=index * 10 + 9), end_day)
            group_end = datetime.combine(group_end_day, datetime.max.time(), UTC).replace(
                microsecond=0,
            )
            _merge_into_period(
                result, item, _format_timestamp(group_start), _format_timestamp(group_end),
            )
        return result

    @staticmethod
    def _bucket_point(value: Mapping[str, Any]) -> dict[str, Any]:
        rates: dict[str, str | None] = {}
        for protocol in PROTOCOL_IDS:
            count = int(value["rate_counts"][protocol])
            total = value["rate_totals"][protocol]
            rates[protocol] = (
                _decimal_text(
                    (Decimal(str(total)) / Decimal(count)).quantize(
                        Decimal("0.000001"), rounding=ROUND_HALF_UP,
                    )
                )
                if count and total is not None else None
            )
        return {
            "observed_at": value["observed_at"],
            "total_position_atomic": value["total_position_atomic"],
            "tracked_earnings_atomic": value["tracked_earnings_atomic"],
            "rates": rates,
        }

    @staticmethod
    def _empty_history(period: str) -> dict[str, Any]:
        return {
            "period": period, "granularity": "none",
            "period_start": None, "period_end": None, "points": [],
        }

    @classmethod
    def unavailable(
        cls, account: Mapping[str, str] | None, history_period: str = "none",
    ) -> dict[str, Any]:
        protocols = [{
            "protocol": protocol,
            "market_id": market,
            "display_name": label,
            "contract_address": contract,
            "position_atomic": None,
            "display_position": None,
            "base_yield": None,
            "incentives": None,
            "confirmed_total_annual_percent": None,
            "total_completeness": "UNAVAILABLE",
            "tracked_earnings_atomic": None,
            "display_tracked_earnings": None,
            "earnings_status": "NOT_ENOUGH_HISTORY",
            "tracked_since": None,
            "data_state": "UNAVAILABLE",
            "observed_at": None,
            "caveats": ["LENDING_PORTFOLIO_UNAVAILABLE"],
        } for (protocol, market, label), contract in zip(
            PROTOCOLS, PINNED_CONTRACTS, strict=True,
        )]
        return {
            "status": "DEGRADED", "authority_available": False,
            "account": dict(account) if account else None,
            "network": dict(NETWORK), "asset": dict(ASSET),
            "summary": {
                "total_position_atomic": None, "display_total_position": None,
                "tracked_earnings_atomic": None, "display_tracked_earnings": None,
                "earnings_status": "NOT_ENOUGH_HISTORY",
                "weighted_confirmed_annual_percent": None,
                "yield_completeness": "PARTIAL",
            },
            "protocols": protocols, "recommendation": None,
            "delivery": cls._delivery(0, int(time.time()), False, "UNAVAILABLE"),
            "history": cls._empty_history(history_period),
            "code": "LENDING_PORTFOLIO_UNAVAILABLE",
            "message": "Lending portfolio is unavailable.",
        }

    @staticmethod
    def _delivery(fetched: int, now: int, forced: bool, source: str) -> dict[str, Any]:
        return {
            "fetched_at": LendingPortfolioService._timestamp(fetched) if fetched else None,
            "cache_age_seconds": max(0, now - fetched) if fetched else 0,
            "cache_max_age_seconds": COMPARE_CACHE_SECONDS,
            "force_refreshed": forced,
            "source": source,
        }

    @staticmethod
    def _display_amount(value: object) -> str | None:
        if value is None:
            return None
        amount = int(str(value))
        whole, fraction = divmod(amount, 10**6)
        suffix = f".{fraction:06d}".rstrip("0").rstrip(".")
        return f"{whole}{suffix} USDC"

    @staticmethod
    def _display_signed(value: int) -> str:
        sign = "+" if value > 0 else "−" if value < 0 else ""
        absolute = LendingPortfolioService._display_amount(abs(value))
        return f"{sign}{absolute}"

    @staticmethod
    def _display_signed_value(value: object) -> str | None:
        return None if value is None else LendingPortfolioService._display_signed(int(str(value)))

    @staticmethod
    def _timestamp(epoch: int) -> str:
        return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
