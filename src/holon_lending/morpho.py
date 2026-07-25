"""Strict identity and historical selection evidence for one Morpho Vault."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
import re
from typing import Any, Mapping

from web3 import Web3


MORPHO_VAULT_ADDRESS = "0xeE8F4eC5672F09119b96Ab6fB59C27E1b7e44b61"
MORPHO_BLUE_ADDRESS = "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"
MIN_TOTAL_ASSETS_ATOMIC = 100_000_000 * 10**6
MIN_LIQUIDITY_ATOMIC = 25_000_000 * 10**6

VAULT_FIELDS = frozenset({
    "address", "creation_block_number", "expected_config", "morpho_address",
    "name", "read_semantics", "selection_evidence", "share_decimals", "symbol",
    "vault_standard", "vault_version",
})
CONFIG_FIELDS = frozenset({"curator", "fee_wad", "guardian", "owner", "timelock_seconds"})
SEMANTIC_FIELDS = frozenset({"allocations", "liquidity", "position", "rate", "rewards"})
EVIDENCE_FIELDS = frozenset({
    "allocations", "api_block_number", "api_timestamp", "featured",
    "liquidity_atomic", "listed", "observed_at", "rpc_block_number",
    "snapshot_only", "total_assets_atomic", "warnings",
})
ALLOCATION_FIELDS = frozenset({
    "collateral_address", "market_id", "supply_assets_atomic", "supply_cap_atomic",
    "supply_queue_index", "withdraw_queue_index",
})

EXPECTED_CONFIG = {
    "curator": "0x9E33faAE38ff641094fa68c65c2cE600b3410585",
    "fee_wad": "0",
    "guardian": "0x7084bF4dB6c21e1834dD6482f6056a39A33584cD",
    "owner": "0x5a4E19842e09000a582c20A4f524C26Fb48Dd4D0",
    "timelock_seconds": 604800,
}
EXPECTED_SEMANTICS = {
    "allocations": "morpho_api.state.allocation",
    "liquidity": "morpho_api.liquidity.underlying",
    "position": "erc4626.balanceOf+convertToAssets",
    "rate": "morpho_api.state.netApyExcludingRewards",
    "rewards": "morpho_api.state.allRewards",
}
EXPECTED_WITHDRAW_QUEUE = (
    "0x38c846197ac32a752a60c25d4536ebb0c3920c532e9a859c38c91efb7b8c2abb",
    "0x9103c3b4e834476c9a62ea009ba2c884ee42e94e6e314a26f04d312434191836",
    "0x13c42741a359ac4a8aa8287d2be109dcf28344484f91185f9a79bd5a805a55ae",
    "0x8793cf302b8ffd655ab97bd1c695dbd967807e8367a65cb2f4edaf1380ba1bda",
    "0xa066f3893b780833699043f824e5bb88b8df039886f524f62b9a1ac83cb7f1f0",
    "0x1c21c59df9db44bf6f645d854ee710a8ca17b479451447e9f56758aee10a2fad",
    "0xdba352d93a64b17c71104cbddc6aef85cd432322a1446b5b65163cbbc615cd0c",
    "0x0ca10126f6c94cbd9cf0a48cc9516ae5e3dec5aa68303e6d988ee37c5149bf0d",
)
EXPECTED_SUPPLY_QUEUE = (EXPECTED_WITHDRAW_QUEUE[1], EXPECTED_WITHDRAW_QUEUE[0])


class MorphoProfileError(ValueError):
    pass


def _object(value: Any, fields: frozenset[str], name: str) -> Mapping[str, Any]:
    if not isinstance(value, dict) or frozenset(value) != fields:
        raise MorphoProfileError(f"{name} fields are invalid")
    return value


def _address(value: Any, name: str) -> str:
    if type(value) is not str or value == ZERO_ADDRESS or not Web3.is_checksum_address(value):
        raise MorphoProfileError(f"{name} is not a checksum address")
    return value


def _atomic(value: Any, name: str) -> int:
    if type(value) is not str or not value.isdigit() or (value != "0" and value.startswith("0")):
        raise MorphoProfileError(f"{name} is not a canonical atomic amount")
    return int(value)


@dataclass(frozen=True)
class MorphoAllocationEvidence:
    collateral_address: str | None
    market_id: str
    supply_assets_atomic: str
    supply_cap_atomic: str
    supply_queue_index: int | None
    withdraw_queue_index: int

    @classmethod
    def from_dict(cls, value: Any) -> "MorphoAllocationEvidence":
        item = _object(value, ALLOCATION_FIELDS, "Morpho allocation")
        market_id = item["market_id"]
        if type(market_id) is not str or re.fullmatch(r"0x[0-9a-f]{64}", market_id) is None:
            raise MorphoProfileError("Morpho market_id is invalid")
        collateral = item["collateral_address"]
        if collateral is not None:
            collateral = _address(collateral, "Morpho collateral")
        _atomic(item["supply_assets_atomic"], "Morpho supplied assets")
        _atomic(item["supply_cap_atomic"], "Morpho supply cap")
        supply_index = item["supply_queue_index"]
        if supply_index is not None and type(supply_index) is not int:
            raise MorphoProfileError("Morpho supply queue index is invalid")
        withdraw_index = item["withdraw_queue_index"]
        if type(withdraw_index) is not int or withdraw_index < 0:
            raise MorphoProfileError("Morpho withdraw queue index is invalid")
        return cls(
            collateral, market_id, item["supply_assets_atomic"],
            item["supply_cap_atomic"], supply_index, withdraw_index,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "collateral_address": self.collateral_address,
            "market_id": self.market_id,
            "supply_assets_atomic": self.supply_assets_atomic,
            "supply_cap_atomic": self.supply_cap_atomic,
            "supply_queue_index": self.supply_queue_index,
            "withdraw_queue_index": self.withdraw_queue_index,
        }


@dataclass(frozen=True)
class MorphoSelectionEvidence:
    allocations: tuple[MorphoAllocationEvidence, ...]
    api_block_number: int
    api_timestamp: int
    liquidity_atomic: str
    observed_at: str
    rpc_block_number: int
    total_assets_atomic: str

    @classmethod
    def from_dict(cls, value: Any) -> "MorphoSelectionEvidence":
        item = _object(value, EVIDENCE_FIELDS, "Morpho selection evidence")
        if item["snapshot_only"] is not True or item["listed"] is not True:
            raise MorphoProfileError("Morpho evidence must be a listed snapshot")
        if item["featured"] is not True or item["warnings"] != []:
            raise MorphoProfileError("Morpho selection evidence has warnings")
        blocks = (item["api_block_number"], item["rpc_block_number"])
        if any(type(block) is not int or block <= 0 for block in blocks):
            raise MorphoProfileError("Morpho evidence block is invalid")
        if type(item["api_timestamp"]) is not int or item["api_timestamp"] <= 0:
            raise MorphoProfileError("Morpho evidence timestamp is invalid")
        if type(item["observed_at"]) is not str or not item["observed_at"].endswith("Z"):
            raise MorphoProfileError("Morpho observed_at is invalid")
        try:
            observed = datetime.fromisoformat(item["observed_at"].replace("Z", "+00:00"))
        except ValueError as exc:
            raise MorphoProfileError("Morpho observed_at is invalid") from exc
        if observed.tzinfo != UTC or int(observed.timestamp()) != item["api_timestamp"]:
            raise MorphoProfileError("Morpho observed_at does not match API time")
        if _atomic(item["total_assets_atomic"], "Morpho total assets") < MIN_TOTAL_ASSETS_ATOMIC:
            raise MorphoProfileError("Morpho total assets gate failed")
        if _atomic(item["liquidity_atomic"], "Morpho liquidity") < MIN_LIQUIDITY_ATOMIC:
            raise MorphoProfileError("Morpho liquidity gate failed")
        raw_allocations = item["allocations"]
        if not isinstance(raw_allocations, list):
            raise MorphoProfileError("Morpho allocations must be an array")
        allocations = tuple(MorphoAllocationEvidence.from_dict(raw) for raw in raw_allocations)
        markets = tuple(allocation.market_id for allocation in allocations)
        if markets != EXPECTED_WITHDRAW_QUEUE or len(set(markets)) != len(markets):
            raise MorphoProfileError("Morpho withdraw queue changed")
        if tuple(allocation.withdraw_queue_index for allocation in allocations) != tuple(range(8)):
            raise MorphoProfileError("Morpho withdraw queue ordering changed")
        supply = sorted(
            (allocation.supply_queue_index, allocation.market_id)
            for allocation in allocations if allocation.supply_queue_index is not None
        )
        if tuple(market_id for _, market_id in supply) != EXPECTED_SUPPLY_QUEUE:
            raise MorphoProfileError("Morpho supply queue changed")
        return cls(
            allocations, blocks[0], item["api_timestamp"], item["liquidity_atomic"],
            item["observed_at"], blocks[1], item["total_assets_atomic"],
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "allocations": [allocation.to_dict() for allocation in self.allocations],
            "api_block_number": self.api_block_number,
            "api_timestamp": self.api_timestamp,
            "featured": True,
            "liquidity_atomic": self.liquidity_atomic,
            "listed": True,
            "observed_at": self.observed_at,
            "rpc_block_number": self.rpc_block_number,
            "snapshot_only": True,
            "total_assets_atomic": self.total_assets_atomic,
            "warnings": [],
        }


@dataclass(frozen=True)
class MorphoVaultReadProfile:
    evidence: MorphoSelectionEvidence

    @classmethod
    def from_dict(cls, value: Any) -> "MorphoVaultReadProfile":
        item = _object(value, VAULT_FIELDS, "Morpho selected vault")
        expected = {
            "address": MORPHO_VAULT_ADDRESS,
            "creation_block_number": 15327791,
            "morpho_address": MORPHO_BLUE_ADDRESS,
            "name": "Gauntlet USDC Prime",
            "share_decimals": 18,
            "symbol": "gtUSDCp",
            "vault_standard": "erc4626",
            "vault_version": "v1",
        }
        for field, expected_value in expected.items():
            if item[field] != expected_value:
                raise MorphoProfileError(f"Morpho {field} changed")
        _address(item["address"], "Morpho Vault")
        _address(item["morpho_address"], "Morpho Blue")
        config = _object(item["expected_config"], CONFIG_FIELDS, "Morpho expected config")
        for field in ("owner", "curator", "guardian"):
            _address(config[field], f"Morpho {field}")
        if config != EXPECTED_CONFIG:
            raise MorphoProfileError("Morpho expected config changed")
        semantics = _object(item["read_semantics"], SEMANTIC_FIELDS, "Morpho read semantics")
        if semantics != EXPECTED_SEMANTICS:
            raise MorphoProfileError("Morpho read semantics changed")
        return cls(MorphoSelectionEvidence.from_dict(item["selection_evidence"]))

    def to_dict(self) -> dict[str, Any]:
        return {
            "address": MORPHO_VAULT_ADDRESS,
            "creation_block_number": 15327791,
            "expected_config": dict(EXPECTED_CONFIG),
            "morpho_address": MORPHO_BLUE_ADDRESS,
            "name": "Gauntlet USDC Prime",
            "read_semantics": dict(EXPECTED_SEMANTICS),
            "selection_evidence": self.evidence.to_dict(),
            "share_decimals": 18,
            "symbol": "gtUSDCp",
            "vault_standard": "erc4626",
            "vault_version": "v1",
        }
