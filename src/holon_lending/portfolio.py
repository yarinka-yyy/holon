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

from .runtime import ASSET, COMPARE_CACHE_SECONDS, NETWORK, PROTOCOLS, LendingReader

ANALYTICS_SCHEMA_VERSION = 1
MAX_ACCOUNTS = 20
MAX_OBSERVATIONS = 2_000
PROTOCOL_IDS = tuple(item[0] for item in PROTOCOLS)
PROTOCOL_LABELS = {item[0]: item[2] for item in PROTOCOLS}
HISTORY_PERIODS = frozenset({"none", "7d", "30d", "all"})


class LendingAnalyticsStore:
    """Best-effort atomic persistence for public Lending observations."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.RLock()

    def load(self, address: str) -> dict[str, Any] | None:
        with self._lock:
            try:
                envelope = self._load_envelope()
                value = next(
                    item for item in envelope["accounts"]
                    if item.get("address") == address
                )
                if not self._valid_account_state(value):
                    return None
                return deepcopy(value)
            except (OSError, ValueError, StopIteration, TypeError, json.JSONDecodeError):
                return None

    def save(self, value: Mapping[str, Any]) -> None:
        address = value.get("address")
        if not isinstance(address, str):
            return
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

    def _load_envelope(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"schema_version": ANALYTICS_SCHEMA_VERSION, "accounts": []}
        with self.path.open("r", encoding="utf-8") as stream:
            value = json.load(stream)
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
        observations = value.get("observations")
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
            or not isinstance(observations, list)
            or len(observations) > MAX_OBSERVATIONS
            or any(not LendingAnalyticsStore._valid_observation(item) for item in observations)
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
    def _valid_observation(value: object) -> bool:
        if (
            not isinstance(value, Mapping)
            or not LendingAnalyticsStore._valid_timestamp(value.get("observed_at"))
        ):
            return False
        return isinstance(value.get("rates"), Mapping)

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
            and len(processed) <= 500
            and all(isinstance(item, str) for item in processed)
            and type(value.get("history_complete")) is bool
        )


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
            self._store.save(account_state)
        except OSError:
            pass
        with self._lock:
            self._cache[address] = (now, deepcopy(response))
        response["history"] = self._filter_history(
            account_state["observations"], history_period, now, history_limit,
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
        response["history"] = self._filter_history(
            stored.get("observations", []), history_period, now, history_limit,
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
            if amount is None and previous is not None:
                amount = previous.get("position_atomic")
            rate = market.get("confirmed_total_annual_percent") if market_live else None
            if rate is None and previous is not None:
                rate = previous.get("confirmed_total_annual_percent")
            data_state = (
                "LIVE" if market_live and position_live
                and market["freshness"]["state"] == "LIVE"
                and position["freshness"]["state"] == "LIVE"
                else "STALE" if market_live and position_live
                else "CACHED" if previous is not None and (amount is not None or rate is not None)
                else "UNAVAILABLE"
            )
            source = market if market_live else previous or {}
            observed = self._observed_at(market, position)
            if observed is None and previous is not None:
                observed = previous.get("observed_at")
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
        timestamps = [
            value["freshness"]["observed_at"] for value in values
            if value and isinstance(value.get("freshness"), Mapping)
            and isinstance(value["freshness"].get("observed_at"), str)
        ]
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
                        or action_id in processed
                        or str(operation.get("updated_at", "")) < str(baseline["started_at"])
                    ):
                        continue
                    amount = operation.get("amount_atomic")
                    if operation.get("verified") is not True or not isinstance(amount, str) or not amount.isdecimal():
                        complete = False
                        processed.add(action_id)
                        continue
                    delta = int(amount)
                    net += delta if operation.get("direction") == "supply" else -delta
                    processed.add(action_id)
            baseline["net_contributions_atomic"] = str(net)
            baseline["processed_action_ids"] = sorted(processed)[-500:]
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
            "history": {"period": "none", "points": []},
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
        observations = list(stored.get("observations", [])) if stored else []
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
                item["protocol"]: item["confirmed_total_annual_percent"]
                for item in response["protocols"]
            },
        }
        minute = observation["observed_at"][:16]
        if observations and str(observations[-1].get("observed_at", ""))[:16] == minute:
            observations[-1] = observation
        else:
            observations.append(observation)
        return {
            "address": account["address"],
            "label": account["label"],
            "saved_at": self._timestamp(now),
            "current": current,
            "baselines": deepcopy(dict(baselines)),
            "observations": observations[-MAX_OBSERVATIONS:],
        }

    def _history(
        self, address: str, period: str, now: int, limit: int,
    ) -> dict[str, Any]:
        stored = self._store.load(address)
        observations = stored.get("observations", []) if stored else []
        return self._filter_history(observations, period, now, limit)

    @staticmethod
    def _filter_history(
        observations: Sequence[Any], period: str, now: int, limit: int,
    ) -> dict[str, Any]:
        if period == "none":
            return {"period": period, "points": []}
        cutoff = None
        if period in {"7d", "30d"}:
            cutoff = datetime.fromtimestamp(now, UTC) - timedelta(days=7 if period == "7d" else 30)
        points = [
            deepcopy(item) for item in observations
            if isinstance(item, Mapping) and (
                cutoff is None
                or datetime.fromisoformat(str(item.get("observed_at", "")).replace("Z", "+00:00")) >= cutoff
            )
        ]
        if limit == 0:
            points = []
        elif len(points) > limit:
            indexes = {
                round(index * (len(points) - 1) / (limit - 1))
                for index in range(limit)
            } if limit > 1 else {len(points) - 1}
            points = [item for index, item in enumerate(points) if index in indexes]
        return {"period": period, "points": points}

    @classmethod
    def unavailable(
        cls, account: Mapping[str, str] | None, history_period: str = "none",
    ) -> dict[str, Any]:
        protocols = [{
            "protocol": protocol,
            "market_id": market,
            "display_name": label,
            "contract_address": None,
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
        } for protocol, market, label in PROTOCOLS]
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
            "history": {"period": history_period, "points": []},
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
