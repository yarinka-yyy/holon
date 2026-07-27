"""Live, read-only Lending L1 adapters and normalized public responses."""

from __future__ import annotations

import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, ROUND_HALF_UP, localcontext
from typing import Any, Callable, Mapping, Protocol
from urllib.request import Request, urlopen

from web3 import Web3

from .model import (
    AAVE_CONTRACTS, BASE_CHAIN_ID, BASE_USDC, COMPOUND_CONTRACTS,
    LendingReadProfiles,
)
from .morpho import MORPHO_VAULT_ADDRESS
from .profiles import ReadProfilesState

SECONDS_PER_YEAR = 31_536_000
ONCHAIN_LIVE_SECONDS = 120
ONCHAIN_UNAVAILABLE_SECONDS = 900
MORPHO_LIVE_SECONDS = 300
MORPHO_UNAVAILABLE_SECONDS = 1_800
FUTURE_TOLERANCE_SECONDS = 60
MAX_RATE = Decimal("10")
MAX_REWARDS = 8
COMPARE_CACHE_SECONDS = 30
DEFAULT_BASE_RPC_URL = "https://base-rpc.publicnode.com"
BASE_RPC_ENV = "HOLON_BASE_RPC_URL"

NETWORK = {"network": "base", "chain_id": BASE_CHAIN_ID}
ASSET = {"asset": "USDC", "address": BASE_USDC, "decimals": 6}
PROTOCOLS = (
    ("aave-v3", "base-usdc", "Aave V3"),
    ("compound-v3", "base-usdc", "Compound III"),
    ("morpho-v1", "gauntlet-usdc-prime-v1", "Morpho Gauntlet USDC Prime"),
)
PINNED_CONTRACTS = (
    AAVE_CONTRACTS["pool"], COMPOUND_CONTRACTS["comet"], MORPHO_VAULT_ADDRESS,
)

AAVE_POOL_ABI = [{
    "type": "function", "name": "getReserveData", "stateMutability": "view",
    "inputs": [{"name": "asset", "type": "address"}],
    "outputs": [{
        "name": "", "type": "tuple", "components": [
            {"name": "configuration", "type": "uint256"},
            {"name": "liquidityIndex", "type": "uint128"},
            {"name": "currentLiquidityRate", "type": "uint128"},
            {"name": "variableBorrowIndex", "type": "uint128"},
            {"name": "currentVariableBorrowRate", "type": "uint128"},
            {"name": "__deprecatedStableBorrowRate", "type": "uint128"},
            {"name": "lastUpdateTimestamp", "type": "uint40"},
            {"name": "id", "type": "uint16"},
            {"name": "aTokenAddress", "type": "address"},
            {"name": "__deprecatedStableDebtTokenAddress", "type": "address"},
            {"name": "variableDebtTokenAddress", "type": "address"},
            {"name": "interestRateStrategyAddress", "type": "address"},
            {"name": "accruedToTreasury", "type": "uint128"},
            {"name": "unbacked", "type": "uint128"},
            {"name": "isolationModeTotalDebt", "type": "uint128"},
        ],
    }],
}]
ADDRESS_GETTER_ABI = lambda name: [{
    "type": "function", "name": name, "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "", "type": "address"}],
}]
UINT_GETTER_ABI = lambda name: [{
    "type": "function", "name": name, "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "", "type": "uint256"}],
}]
BALANCE_ABI = [{
    "type": "function", "name": "balanceOf", "stateMutability": "view",
    "inputs": [{"name": "account", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}],
}]
COMPOUND_RATE_ABI = [{
    "type": "function", "name": "getSupplyRate", "stateMutability": "view",
    "inputs": [{"name": "utilization", "type": "uint256"}],
    "outputs": [{"name": "", "type": "uint64"}],
}]
CONVERT_ABI = [{
    "type": "function", "name": "convertToAssets", "stateMutability": "view",
    "inputs": [{"name": "shares", "type": "uint256"}],
    "outputs": [{"name": "", "type": "uint256"}],
}]
STRING_GETTER_ABI = lambda name: [{
    "type": "function", "name": name, "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "", "type": "string"}],
}]


class LendingReadError(RuntimeError):
    pass


class LendingReader(Protocol):
    def compare(self, force_refresh: bool = False) -> dict[str, Any]: ...

    def positions(self, account: Mapping[str, str] | None) -> dict[str, Any]: ...


def _timestamp(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _freshness(epoch: int, block: int, now: int, *, api: bool = False) -> dict[str, Any]:
    age = now - epoch
    live = MORPHO_LIVE_SECONDS if api else ONCHAIN_LIVE_SECONDS
    unavailable = MORPHO_UNAVAILABLE_SECONDS if api else ONCHAIN_UNAVAILABLE_SECONDS
    if epoch <= 0 or block <= 0 or age < -FUTURE_TOLERANCE_SECONDS or age > unavailable:
        return {"state": "UNAVAILABLE", "observed_at": None, "block_number": None}
    state = "LIVE" if age <= live else "STALE"
    return {"state": state, "observed_at": _timestamp(epoch), "block_number": block}


def _worst_freshness(first: Mapping[str, Any], second: Mapping[str, Any]) -> dict[str, Any]:
    order = {"LIVE": 0, "STALE": 1, "UNAVAILABLE": 2}
    if order[first["state"]] >= order[second["state"]]:
        return dict(first)
    return dict(second)


def _canonical_decimal(value: Decimal, places: int = 6) -> str:
    if not value.is_finite():
        raise LendingReadError("Rate is not finite")
    quantized = value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    text = format(quantized, "f").rstrip("0").rstrip(".")
    return text or "0"


def _rate(value: Any) -> Decimal:
    try:
        result = value if isinstance(value, Decimal) else Decimal(str(value))
    except Exception as exc:
        raise LendingReadError("Rate is invalid") from exc
    if not result.is_finite() or result < 0 or result > MAX_RATE:
        raise LendingReadError("Rate is out of bounds")
    return result


def _apy_from_apr(apr: Decimal) -> Decimal:
    with localcontext() as context:
        context.prec = 50
        return (Decimal(1) + apr / SECONDS_PER_YEAR) ** SECONDS_PER_YEAR - Decimal(1)


def _display_amount(atomic: int) -> str:
    whole, fraction = divmod(atomic, 10**6)
    suffix = f".{fraction:06d}".rstrip("0").rstrip(".")
    return f"{whole}{suffix} USDC"


def _unavailable_freshness() -> dict[str, Any]:
    return {"state": "UNAVAILABLE", "observed_at": None, "block_number": None}


def _incentives_unavailable() -> dict[str, Any]:
    return {"status": "UNAVAILABLE", "total_apr_percent": None, "components": []}


def _market_unavailable(protocol: str, market_id: str, contract: str, caveat: str) -> dict[str, Any]:
    return {
        "protocol": protocol, "market_id": market_id, "contract_address": contract,
        "base_yield": None, "incentives": _incentives_unavailable(),
        "confirmed_total_annual_percent": None, "total_completeness": "UNAVAILABLE",
        "freshness": _unavailable_freshness(), "caveats": [caveat],
    }


def _position_unavailable(protocol: str, market_id: str, contract: str, caveat: str) -> dict[str, Any]:
    return {
        "protocol": protocol, "market_id": market_id, "contract_address": contract,
        "amount_atomic": None, "decimals": 6, "display_amount": None,
        "freshness": _unavailable_freshness(), "caveats": [caveat],
    }


@dataclass(frozen=True)
class ChainSnapshot:
    block_number: int
    timestamp: int


class BaseRpcReader:
    def __init__(self, endpoint: str, profiles: LendingReadProfiles, timeout: float = 5.0) -> None:
        self.profiles = profiles
        self.web3 = Web3(Web3.HTTPProvider(endpoint, request_kwargs={"timeout": timeout}))

    def begin(self) -> ChainSnapshot:
        if self.web3.eth.chain_id != BASE_CHAIN_ID:
            raise LendingReadError("Wrong Base chain")
        block = self.web3.eth.get_block("latest")
        return ChainSnapshot(int(block["number"]), int(block["timestamp"]))

    def _contract(self, address: str, abi: list[dict[str, Any]]):
        if not self.web3.eth.get_code(address):
            raise LendingReadError("Contract bytecode is unavailable")
        return self.web3.eth.contract(address=address, abi=abi)

    def aave(self, snapshot: ChainSnapshot, account: str | None = None) -> tuple[int, int | None]:
        contracts = dict(self.profiles.protocols[0].contracts)
        usdc = self._contract(BASE_USDC, UINT_GETTER_ABI("decimals"))
        if usdc.functions.decimals().call(block_identifier=snapshot.block_number) != 6:
            raise LendingReadError("USDC decimals changed")
        provider = self._contract(contracts["pool_addresses_provider"], ADDRESS_GETTER_ABI("getPool"))
        if Web3.to_checksum_address(provider.functions.getPool().call(block_identifier=snapshot.block_number)) != contracts["pool"]:
            raise LendingReadError("Aave Pool identity changed")
        pool = self._contract(contracts["pool"], AAVE_POOL_ABI)
        reserve = pool.functions.getReserveData(BASE_USDC).call(block_identifier=snapshot.block_number)
        if len(reserve) != 15 or Web3.to_checksum_address(reserve[8]) != contracts["a_token"]:
            raise LendingReadError("Aave reserve identity changed")
        balance = None
        if account is not None:
            token = self._contract(contracts["a_token"], BALANCE_ABI)
            balance = int(token.functions.balanceOf(account).call(block_identifier=snapshot.block_number))
        return int(reserve[2]), balance

    def compound(self, snapshot: ChainSnapshot, account: str | None = None) -> tuple[int, int, int | None]:
        contracts = dict(self.profiles.protocols[1].contracts)
        abi = ADDRESS_GETTER_ABI("baseToken") + UINT_GETTER_ABI("getUtilization") + COMPOUND_RATE_ABI + BALANCE_ABI
        comet = self._contract(contracts["comet"], abi)
        if Web3.to_checksum_address(comet.functions.baseToken().call(block_identifier=snapshot.block_number)) != BASE_USDC:
            raise LendingReadError("Compound base token changed")
        utilization = int(comet.functions.getUtilization().call(block_identifier=snapshot.block_number))
        supply_rate = int(comet.functions.getSupplyRate(utilization).call(block_identifier=snapshot.block_number))
        balance = None if account is None else int(comet.functions.balanceOf(account).call(block_identifier=snapshot.block_number))
        return utilization, supply_rate, balance

    def morpho(self, snapshot: ChainSnapshot, account: str | None = None) -> int | None:
        selected = self.profiles.selected_morpho_vault
        value = selected.to_dict()
        abi = (
            ADDRESS_GETTER_ABI("asset") + UINT_GETTER_ABI("decimals")
            + STRING_GETTER_ABI("name") + STRING_GETTER_ABI("symbol")
            + BALANCE_ABI + CONVERT_ABI
        )
        vault = self._contract(MORPHO_VAULT_ADDRESS, abi)
        block = snapshot.block_number
        if Web3.to_checksum_address(vault.functions.asset().call(block_identifier=block)) != BASE_USDC:
            raise LendingReadError("Morpho asset changed")
        if vault.functions.decimals().call(block_identifier=block) != value["share_decimals"]:
            raise LendingReadError("Morpho share decimals changed")
        if vault.functions.name().call(block_identifier=block) != value["name"] or vault.functions.symbol().call(block_identifier=block) != value["symbol"]:
            raise LendingReadError("Morpho identity changed")
        if account is None:
            return None
        shares = int(vault.functions.balanceOf(account).call(block_identifier=block))
        return int(vault.functions.convertToAssets(shares).call(block_identifier=block))


MORPHO_QUERY = """query HolonVault {
  vaultByAddress(address: \"%s\", chainId: 8453) {
    address name symbol listed featured warnings { type level }
    asset { address decimals }
    state {
      netApyExcludingRewards timestamp blockNumber
      allRewards { asset { address chain { id } } supplyApr }
    }
  }
}""" % MORPHO_VAULT_ADDRESS


class MorphoApiClient:
    def __init__(self, endpoint: str, timeout: float = 5.0) -> None:
        self.endpoint = endpoint
        self.timeout = timeout

    def query(self) -> Mapping[str, Any]:
        body = json.dumps({"query": MORPHO_QUERY}, separators=(",", ":")).encode()
        request = Request(
            self.endpoint, data=body,
            headers={"Content-Type": "application/json", "User-Agent": "Holon/0.1"},
            method="POST",
        )
        with urlopen(request, timeout=self.timeout) as response:
            raw = response.read(64 * 1024 + 1)
        if len(raw) > 64 * 1024:
            raise LendingReadError("Morpho response is too large")
        value = json.loads(raw.decode(), parse_float=Decimal)
        if not isinstance(value, dict) or set(value) not in ({"data"}, {"data", "extensions"}):
            raise LendingReadError("Morpho response is invalid")
        if "extensions" in value:
            extensions = value["extensions"]
            if (
                not isinstance(extensions, dict)
                or set(extensions) != {"complexity", "maximumComplexity"}
                or any(type(item) is not int or item < 0 for item in extensions.values())
            ):
                raise LendingReadError("Morpho response metadata is invalid")
        data = value["data"]
        if not isinstance(data, dict) or set(data) != {"vaultByAddress"}:
            raise LendingReadError("Morpho data is invalid")
        return data["vaultByAddress"]


class LendingReadService:
    def __init__(
        self, profiles_state: ReadProfilesState, rpc_factory: Callable[[], BaseRpcReader],
        morpho: MorphoApiClient, clock: Callable[[], float] = time.time,
    ) -> None:
        self._state = profiles_state
        self._rpc_factory = rpc_factory
        self._morpho = morpho
        self._clock = clock
        self._compare_cache: dict[str, Any] | None = None
        self._compare_cached_at: int | None = None

    @classmethod
    def default(cls, environ: Mapping[str, str] | None = None) -> "LendingReadService":
        values = os.environ if environ is None else environ
        state = ReadProfilesState.load()
        endpoint = values.get(BASE_RPC_ENV, DEFAULT_BASE_RPC_URL).strip()
        profiles = state.profiles
        return cls(
            state,
            lambda: BaseRpcReader(endpoint, profiles) if profiles is not None else (_ for _ in ()).throw(LendingReadError("Profiles unavailable")),
            MorphoApiClient("https://api.morpho.org/graphql"),
        )

    @classmethod
    def unavailable(cls, code: str = "READ_PROFILES_UNAVAILABLE") -> "LendingReadService":
        state = ReadProfilesState("UNAVAILABLE", None, code)
        return cls(
            state,
            lambda: (_ for _ in ()).throw(LendingReadError("Lending unavailable")),
            MorphoApiClient("https://api.morpho.org/graphql"),
        )

    @staticmethod
    def _retry(call: Callable[[], Any]) -> Any:
        try:
            return call()
        except LendingReadError:
            raise
        except Exception:
            return call()

    def _profile_contracts(self) -> tuple[str, str, str]:
        assert self._state.profiles is not None
        return (
            dict(self._state.profiles.protocols[0].contracts)["pool"],
            dict(self._state.profiles.protocols[1].contracts)["comet"],
            MORPHO_VAULT_ADDRESS,
        )

    def _unavailable_compare(self, code: str) -> dict[str, Any]:
        contracts = self._profile_contracts() if self._state.profiles else PINNED_CONTRACTS
        markets = [
            _market_unavailable(protocol, market, contract, code)
            for (protocol, market, _), contract in zip(PROTOCOLS, contracts, strict=True)
        ]
        return self._compare_response(markets)

    def compare(self, force_refresh: bool = False) -> dict[str, Any]:
        if type(force_refresh) is not bool:
            raise LendingReadError("force_refresh must be boolean")
        if self._state.profiles is None:
            return self._with_delivery(
                self._unavailable_compare(self._state.error_code or "READ_PROFILES_UNAVAILABLE"),
                int(self._clock()), 0, force_refresh,
            )
        contracts = self._profile_contracts()
        now = int(self._clock())
        if (
            not force_refresh and self._compare_cache is not None
            and self._compare_cached_at is not None
            and 0 <= now - self._compare_cached_at <= COMPARE_CACHE_SECONDS
        ):
            return self._with_delivery(
                deepcopy(self._compare_cache), self._compare_cached_at,
                now - self._compare_cached_at, False,
            )
        try:
            rpc = self._rpc_factory()
            snapshot = self._retry(rpc.begin)
            chain_freshness = _freshness(snapshot.timestamp, snapshot.block_number, now)
        except Exception:
            rpc = None
            snapshot = None
            chain_freshness = _unavailable_freshness()

        def aave() -> dict[str, Any]:
            try:
                if rpc is None or snapshot is None or chain_freshness["state"] == "UNAVAILABLE":
                    raise LendingReadError("On-chain data unavailable")
                ray, _ = self._retry(lambda: rpc.aave(snapshot))
                apr = _rate(Decimal(ray) / Decimal(10**27))
                return self._market_rate(
                    PROTOCOLS[0][0], PROTOCOLS[0][1], contracts[0], str(ray), "ray_apr",
                    apr, "APR", _apy_from_apr(apr), "per_second_compounding_365d",
                    chain_freshness, _incentives_unavailable(), ["INCENTIVES_NOT_PROFILED"],
                )
            except Exception:
                return _market_unavailable(*PROTOCOLS[0][:2], contracts[0], "AAVE_DATA_UNAVAILABLE")

        def compound() -> dict[str, Any]:
            try:
                if rpc is None or snapshot is None or chain_freshness["state"] == "UNAVAILABLE":
                    raise LendingReadError("On-chain data unavailable")
                _utilization, per_second, _ = self._retry(lambda: rpc.compound(snapshot))
                per_second_decimal = _rate(Decimal(per_second) / Decimal(10**18))
                apr = _rate(per_second_decimal * SECONDS_PER_YEAR)
                apy = (Decimal(1) + per_second_decimal) ** SECONDS_PER_YEAR - Decimal(1)
                return self._market_rate(
                    PROTOCOLS[1][0], PROTOCOLS[1][1], contracts[1], str(per_second),
                    "per_second_wad", apr, "APR", apy, "per_second_compounding_365d",
                    chain_freshness, _incentives_unavailable(), ["INCENTIVES_NOT_PROFILED"],
                )
            except Exception:
                return _market_unavailable(*PROTOCOLS[1][:2], contracts[1], "COMPOUND_DATA_UNAVAILABLE")

        def morpho() -> dict[str, Any]:
            try:
                if rpc is None or snapshot is None or chain_freshness["state"] == "UNAVAILABLE":
                    raise LendingReadError("On-chain identity unavailable")
                self._retry(lambda: rpc.morpho(snapshot))
                raw = self._retry(self._morpho.query)
                rate, incentives, api_freshness = self._parse_morpho(raw, now)
                freshness = _worst_freshness(chain_freshness, api_freshness)
                if freshness["state"] == "UNAVAILABLE":
                    raise LendingReadError("Morpho data is stale")
                return self._market_rate(
                    PROTOCOLS[2][0], PROTOCOLS[2][1], contracts[2], str(rate),
                    "decimal_fraction", rate, "APY", rate, "reported_apy",
                    freshness, incentives, [],
                )
            except Exception:
                return _market_unavailable(*PROTOCOLS[2][:2], contracts[2], "MORPHO_DATA_UNAVAILABLE")

        with ThreadPoolExecutor(max_workers=3, thread_name_prefix="holon-lending") as executor:
            futures = [executor.submit(call) for call in (aave, compound, morpho)]
            markets = [future.result() for future in futures]
        result = self._compare_response(markets)
        self._compare_cache = deepcopy(result)
        self._compare_cached_at = now
        return self._with_delivery(result, now, 0, force_refresh)

    @staticmethod
    def _market_rate(
        protocol: str, market: str, contract: str, raw: str, raw_unit: str,
        base: Decimal, metric: str, apy: Decimal, normalization: str,
        freshness: Mapping[str, Any], incentives: Mapping[str, Any], caveats: list[str],
    ) -> dict[str, Any]:
        incentive_apr = (
            Decimal(str(incentives["total_apr_percent"]))
            if incentives.get("status") == "AVAILABLE" else Decimal(0)
        )
        total = _rate(apy) * 100 + incentive_apr
        return {
            "protocol": protocol, "market_id": market, "contract_address": contract,
            "base_yield": {
                "source_raw_value": raw, "source_raw_unit": raw_unit,
                "value_percent": _canonical_decimal(base * 100), "metric": metric,
                "comparison_apy_percent": _canonical_decimal(_rate(apy) * 100),
                "normalization": normalization,
            },
            "incentives": dict(incentives),
            "confirmed_total_annual_percent": _canonical_decimal(total),
            "total_completeness": (
                "BASE_AND_INCENTIVES" if incentives.get("status") == "AVAILABLE"
                else "BASE_ONLY"
            ),
            "freshness": dict(freshness),
            "caveats": caveats,
        }

    @staticmethod
    def _parse_morpho(value: Mapping[str, Any], now: int) -> tuple[Decimal, dict[str, Any], dict[str, Any]]:
        fields = {"address", "name", "symbol", "listed", "featured", "warnings", "asset", "state"}
        if not isinstance(value, Mapping) or set(value) != fields:
            raise LendingReadError("Morpho Vault fields changed")
        if (
            Web3.to_checksum_address(value["address"]) != MORPHO_VAULT_ADDRESS
            or value["name"] != "Gauntlet USDC Prime" or value["symbol"] != "gtUSDCp"
            or value["listed"] is not True or not isinstance(value["featured"], bool)
            or value["warnings"] != []
        ):
            raise LendingReadError("Morpho Vault identity changed")
        asset = value["asset"]
        if not isinstance(asset, Mapping) or set(asset) != {"address", "decimals"}:
            raise LendingReadError("Morpho asset is invalid")
        if Web3.to_checksum_address(asset["address"]) != BASE_USDC or asset["decimals"] != 6:
            raise LendingReadError("Morpho asset changed")
        state = value["state"]
        expected = {"netApyExcludingRewards", "timestamp", "blockNumber", "allRewards"}
        if not isinstance(state, Mapping) or set(state) != expected:
            raise LendingReadError("Morpho state fields changed")
        rate = _rate(state["netApyExcludingRewards"])
        timestamp, block = state["timestamp"], state["blockNumber"]
        if type(timestamp) is not int or type(block) is not int:
            raise LendingReadError("Morpho freshness is invalid")
        freshness = _freshness(timestamp, block, now, api=True)
        rewards = state["allRewards"]
        if not isinstance(rewards, list) or len(rewards) > MAX_REWARDS:
            raise LendingReadError("Morpho rewards are invalid")
        components: list[dict[str, str]] = []
        total = Decimal(0)
        for reward in rewards:
            if not isinstance(reward, Mapping) or set(reward) != {"asset", "supplyApr"}:
                raise LendingReadError("Morpho reward fields changed")
            asset_value = reward["asset"]
            if not isinstance(asset_value, Mapping) or set(asset_value) != {"address", "chain"}:
                raise LendingReadError("Morpho reward asset is invalid")
            chain = asset_value["chain"]
            if not isinstance(chain, Mapping) or chain != {"id": BASE_CHAIN_ID}:
                raise LendingReadError("Morpho reward chain changed")
            address = Web3.to_checksum_address(asset_value["address"])
            apr = _rate(reward["supplyApr"])
            total += apr
            components.append({"asset_address": address, "apr_percent": _canonical_decimal(apr * 100)})
        incentives = {
            "status": "AVAILABLE", "total_apr_percent": _canonical_decimal(total * 100),
            "components": components,
        }
        return rate, incentives, freshness

    @staticmethod
    def _compare_response(markets: list[dict[str, Any]]) -> dict[str, Any]:
        usable = [item for item in markets if item["freshness"]["state"] in {"LIVE", "STALE"}]
        live = sum(item["freshness"]["state"] == "LIVE" for item in markets)
        if live == 3:
            status, code, message = "READY", "LENDING_MARKETS_READY", "Lending markets are available."
        elif usable:
            status, code, message = "PARTIAL", "LENDING_MARKETS_PARTIAL", "Some Lending markets are unavailable or stale."
        else:
            status, code, message = "DEGRADED", "LENDING_UNAVAILABLE", "Lending data is unavailable."
        highest = None
        recommendation = None
        if usable:
            item = max(usable, key=lambda entry: Decimal(entry["base_yield"]["comparison_apy_percent"]))
            highest = {
                "protocol": item["protocol"],
                "comparison_apy_percent": item["base_yield"]["comparison_apy_percent"],
                "not_safety_recommendation": True,
            }
            recommended = max(
                usable, key=lambda entry: Decimal(entry["confirmed_total_annual_percent"]),
            )
            missing = [
                entry["protocol"] for entry in usable
                if entry["total_completeness"] == "BASE_ONLY"
            ]
            recommendation = {
                "protocol": recommended["protocol"],
                "confirmed_total_annual_percent": recommended["confirmed_total_annual_percent"],
                "missing_incentive_protocols": missing,
                "incomplete_comparison": bool(missing) or len(usable) < len(markets),
                "requires_user_confirmation": True,
            }
        return {
            "status": status, "authority_available": False, "network": dict(NETWORK),
            "asset": dict(ASSET), "markets": markets, "highest_observed": highest,
            "recommendation": recommendation,
            "code": code, "message": message,
        }

    @staticmethod
    def _with_delivery(
        payload: dict[str, Any], fetched_at: int, age: int, force_refreshed: bool,
    ) -> dict[str, Any]:
        payload["delivery"] = {
            "fetched_at": _timestamp(fetched_at), "cache_age_seconds": age,
            "cache_max_age_seconds": COMPARE_CACHE_SECONDS,
            "force_refreshed": force_refreshed,
        }
        return payload

    def positions(self, account: Mapping[str, str] | None) -> dict[str, Any]:
        if self._state.profiles is None:
            return self._positions_unavailable(account, self._state.error_code or "READ_PROFILES_UNAVAILABLE")
        if account is None:
            return self._positions_unavailable(None, "WALLET_ACCOUNT_UNAVAILABLE")
        address = account.get("address")
        label = account.get("label")
        if not isinstance(address, str) or not Web3.is_checksum_address(address) or not isinstance(label, str):
            return self._positions_unavailable(None, "WALLET_ACCOUNT_UNAVAILABLE")
        contracts = self._profile_contracts()
        try:
            rpc = self._rpc_factory()
            snapshot = self._retry(rpc.begin)
            freshness = _freshness(snapshot.timestamp, snapshot.block_number, int(self._clock()))
            if freshness["state"] == "UNAVAILABLE":
                raise LendingReadError("On-chain snapshot unavailable")
        except Exception:
            return self._positions_unavailable(dict(account), "BASE_RPC_UNAVAILABLE")
        positions: list[dict[str, Any]] = []
        calls = (
            lambda: self._retry(lambda: rpc.aave(snapshot, address))[1],
            lambda: self._retry(lambda: rpc.compound(snapshot, address))[2],
            lambda: self._retry(lambda: rpc.morpho(snapshot, address)),
        )
        errors = ("AAVE_POSITION_UNAVAILABLE", "COMPOUND_POSITION_UNAVAILABLE", "MORPHO_POSITION_UNAVAILABLE")
        for index, call in enumerate(calls):
            protocol, market, _ = PROTOCOLS[index]
            try:
                amount = call()
                if type(amount) is not int or amount < 0 or amount >= 2**256:
                    raise LendingReadError("Position is invalid")
                positions.append({
                    "protocol": protocol, "market_id": market,
                    "contract_address": contracts[index], "amount_atomic": str(amount),
                    "decimals": 6, "display_amount": _display_amount(amount),
                    "freshness": dict(freshness), "caveats": [],
                })
            except Exception:
                positions.append(_position_unavailable(protocol, market, contracts[index], errors[index]))
        usable = [item for item in positions if item["freshness"]["state"] in {"LIVE", "STALE"}]
        live = sum(item["freshness"]["state"] == "LIVE" for item in positions)
        if live == 3:
            status, code, message = "READY", "LENDING_POSITIONS_READY", "Lending positions are available."
        elif usable:
            status, code, message = "PARTIAL", "LENDING_POSITIONS_PARTIAL", "Some Lending positions are unavailable or stale."
        else:
            status, code, message = "DEGRADED", "LENDING_POSITIONS_UNAVAILABLE", "Lending positions are unavailable."
        return {
            "status": status, "authority_available": False, "account": dict(account),
            "network": dict(NETWORK), "asset": dict(ASSET), "positions": positions,
            "code": code, "message": message,
        }

    def _positions_unavailable(self, account: Mapping[str, str] | None, caveat: str) -> dict[str, Any]:
        contracts = self._profile_contracts() if self._state.profiles else PINNED_CONTRACTS
        positions = [
            _position_unavailable(protocol, market, contract, caveat)
            for (protocol, market, _), contract in zip(PROTOCOLS, contracts, strict=True)
        ]
        return {
            "status": "DEGRADED", "authority_available": False,
            "account": dict(account) if account is not None else None,
            "network": dict(NETWORK), "asset": dict(ASSET), "positions": positions,
            "code": "LENDING_POSITIONS_UNAVAILABLE",
            "message": "Lending positions are unavailable.",
        }
