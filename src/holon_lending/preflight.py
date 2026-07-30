"""Non-authoritative Aave Base USDC action preview and preflight."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import Enum
from typing import Any, Callable, Mapping, Protocol

from web3 import Web3
from web3.exceptions import BadFunctionCallOutput, ContractLogicError

from .action_profiles import ActionProfilesState, AaveActionProfile, ProtocolActionProfile
from .runtime import BASE_RPC_ENV, DEFAULT_BASE_RPC_URL

PREVIEW_LIFETIME = timedelta(minutes=5)
BLOCK_MAX_AGE_SECONDS = 120
FUTURE_TOLERANCE_SECONDS = 60
MAX_UINT256 = 2**256 - 1
BASE_GAS_PRICE_ORACLE = "0x420000000000000000000000000000000000000F"
LENDING_TRANSACTION_SIZE_UPPER_BOUND = 512
GAS_ESTIMATE_HEADROOM_BPS = 2_000
GAS_BPS_DENOMINATOR = 10_000
AMOUNT_RE = re.compile(r"^[0-9]+(?:[.,][0-9]+)?$")
ALCHEMY_BASE_RPC_URL = "https://base-mainnet.g.alchemy.com/public"


def gas_with_headroom(estimate: int) -> int:
    if estimate <= 0:
        raise LendingPreflightError(LendingPreflightCode.GAS_ESTIMATE_FAILED)
    return (
        estimate * (GAS_BPS_DENOMINATOR + GAS_ESTIMATE_HEADROOM_BPS)
        + GAS_BPS_DENOMINATOR - 1
    ) // GAS_BPS_DENOMINATOR
ADDRESS_ABI = lambda name: [{
    "type": "function", "name": name, "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "", "type": "address"}],
}]
UINT_ABI = lambda name: [{
    "type": "function", "name": name, "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "", "type": "uint256"}],
}]
BALANCE_ABI = [{
    "type": "function", "name": "balanceOf", "stateMutability": "view",
    "inputs": [{"name": "account", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}],
}]
ALLOWANCE_ABI = [{
    "type": "function", "name": "allowance", "stateMutability": "view",
    "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}],
}]
RESERVE_TOKENS_ABI = [{
    "type": "function", "name": "getReserveTokensAddresses", "stateMutability": "view",
    "inputs": [{"name": "asset", "type": "address"}],
    "outputs": [
        {"name": "aTokenAddress", "type": "address"},
        {"name": "stableDebtTokenAddress", "type": "address"},
        {"name": "variableDebtTokenAddress", "type": "address"},
    ],
}]
RESERVE_CONFIG_ABI = [{
    "type": "function", "name": "getReserveConfigurationData", "stateMutability": "view",
    "inputs": [{"name": "asset", "type": "address"}],
    "outputs": [
        {"name": "decimals", "type": "uint256"}, {"name": "ltv", "type": "uint256"},
        {"name": "liquidationThreshold", "type": "uint256"},
        {"name": "liquidationBonus", "type": "uint256"},
        {"name": "reserveFactor", "type": "uint256"},
        {"name": "usageAsCollateralEnabled", "type": "bool"},
        {"name": "borrowingEnabled", "type": "bool"},
        {"name": "stableBorrowRateEnabled", "type": "bool"},
        {"name": "isActive", "type": "bool"}, {"name": "isFrozen", "type": "bool"},
    ],
}]
RESERVE_CAPS_ABI = [{
    "type": "function", "name": "getReserveCaps", "stateMutability": "view",
    "inputs": [{"name": "asset", "type": "address"}],
    "outputs": [{"name": "borrowCap", "type": "uint256"}, {"name": "supplyCap", "type": "uint256"}],
}]
PAUSED_ABI = [{
    "type": "function", "name": "getPaused", "stateMutability": "view",
    "inputs": [{"name": "asset", "type": "address"}],
    "outputs": [{"name": "isPaused", "type": "bool"}],
}]
RESERVE_DATA_ABI = [{
    "type": "function", "name": "getReserveData", "stateMutability": "view",
    "inputs": [{"name": "asset", "type": "address"}],
    "outputs": [
        {"name": "unbacked", "type": "uint256"},
        {"name": "accruedToTreasuryScaled", "type": "uint256"},
        {"name": "totalAToken", "type": "uint256"},
        {"name": "totalStableDebt", "type": "uint256"},
        {"name": "totalVariableDebt", "type": "uint256"},
        {"name": "liquidityRate", "type": "uint256"},
        {"name": "variableBorrowRate", "type": "uint256"},
        {"name": "stableBorrowRate", "type": "uint256"},
        {"name": "averageStableBorrowRate", "type": "uint256"},
        {"name": "liquidityIndex", "type": "uint256"},
        {"name": "variableBorrowIndex", "type": "uint256"},
        {"name": "lastUpdateTimestamp", "type": "uint40"},
    ],
}]
ACCOUNT_DATA_ABI = [{
    "type": "function", "name": "getUserAccountData", "stateMutability": "view",
    "inputs": [{"name": "user", "type": "address"}],
    "outputs": [
        {"name": "totalCollateralBase", "type": "uint256"},
        {"name": "totalDebtBase", "type": "uint256"},
        {"name": "availableBorrowsBase", "type": "uint256"},
        {"name": "currentLiquidationThreshold", "type": "uint256"},
        {"name": "ltv", "type": "uint256"},
        {"name": "healthFactor", "type": "uint256"},
    ],
}]
L1_FEE_UPPER_BOUND_ABI = [{
    "type": "function", "name": "getL1FeeUpperBound", "stateMutability": "view",
    "inputs": [{"name": "unsignedTxSize", "type": "uint256"}],
    "outputs": [{"name": "", "type": "uint256"}],
}]
BOOL_ABI = lambda name: [{
    "type": "function", "name": name, "stateMutability": "view",
    "inputs": [], "outputs": [{"name": "", "type": "bool"}],
}]
ACCOUNT_UINT_ABI = lambda name: [{
    "type": "function", "name": name, "stateMutability": "view",
    "inputs": [{"name": "account", "type": "address"}],
    "outputs": [{"name": "", "type": "uint256"}],
}]
VAULT_UINT_ABI = lambda name: [{
    "type": "function", "name": name, "stateMutability": "view",
    "inputs": [{"name": "value", "type": "uint256"}],
    "outputs": [{"name": "", "type": "uint256"}],
}]


class LendingPreflightCode(str, Enum):
    REQUEST_INVALID = "LENDING_ACTION_INVALID"
    PROFILE_UNAVAILABLE = "ACTION_PROFILES_UNAVAILABLE"
    PROFILE_MISMATCH = "ACTION_PROFILE_MISMATCH"
    ACCOUNT_UNAVAILABLE = "WALLET_ACCOUNT_UNAVAILABLE"
    ACCOUNT_CHANGED = "ACCOUNT_CHANGED"
    RPC_UNAVAILABLE = "BASE_RPC_UNAVAILABLE"
    WRONG_CHAIN = "WRONG_CHAIN"
    IDENTITY_MISMATCH = "AAVE_IDENTITY_MISMATCH"
    STALE_BLOCK = "AAVE_BLOCK_STALE"
    RESERVE_INACTIVE = "AAVE_RESERVE_INACTIVE"
    RESERVE_FROZEN = "AAVE_RESERVE_FROZEN"
    RESERVE_PAUSED = "AAVE_RESERVE_PAUSED"
    ACCOUNT_HAS_DEBT = "AAVE_ACCOUNT_HAS_DEBT"
    INSUFFICIENT_USDC = "INSUFFICIENT_USDC"
    INSUFFICIENT_POSITION = "INSUFFICIENT_AUSDC"
    INSUFFICIENT_LIQUIDITY = "INSUFFICIENT_PROTOCOL_LIQUIDITY"
    SUPPLY_CAP_REACHED = "AAVE_SUPPLY_CAP_REACHED"
    UNEXPECTED_ALLOWANCE = "UNEXPECTED_ALLOWANCE"
    INSUFFICIENT_ETH = "INSUFFICIENT_ETH"
    GAS_ESTIMATE_FAILED = "GAS_ESTIMATE_FAILED"
    SIMULATION_FAILED = "SIMULATION_FAILED"


class LendingPreflightError(RuntimeError):
    def __init__(self, code: LendingPreflightCode | str) -> None:
        value = code.value if isinstance(code, LendingPreflightCode) else code
        super().__init__(value)
        self.code = value


@dataclass(frozen=True, slots=True)
class LendingIntent:
    profile_id: str
    protocol_id: str
    action: str
    amount_mode: str
    amount: str | None
    amount_atomic: int | None


def parse_lending_intent(value: Mapping[str, object]) -> LendingIntent:
    expected = {
        "module_id": "lending", "module_version": "1",
        "protocol_profile_version": "1", "network": "base", "asset": "usdc",
        "beneficiary_mode": "active_wallet_account",
    }
    if set(value) != {*expected, "protocol_profile_id", "action", "amount_mode", "amount"}:
        raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
    if any(value.get(name) != expected_value for name, expected_value in expected.items()):
        raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
    profiles = {
        "aave-v3-base-usdc": "aave-v3",
        "compound-v3-base-usdc": "compound-v3",
        "morpho-v1-gauntlet-usdc-prime": "morpho-v1",
    }
    profile_id = value.get("protocol_profile_id")
    if profile_id not in profiles:
        raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
    action, mode, amount = value.get("action"), value.get("amount_mode"), value.get("amount")
    if action not in {"supply", "withdraw"} or mode not in {"exact", "all"}:
        raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
    if mode == "all":
        if amount is not None:
            raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
        return LendingIntent(str(profile_id), profiles[str(profile_id)], action, mode, None, None)
    if not isinstance(amount, str) or len(amount) > 80 or AMOUNT_RE.fullmatch(amount) is None:
        raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
    normalized = amount.replace(",", ".")
    whole, separator, fraction = normalized.partition(".")
    if len(fraction) > 6:
        raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
    atomic = int(whole) * 10**6
    if separator:
        atomic += int(fraction.ljust(6, "0"))
    if atomic <= 0 or atomic > MAX_UINT256:
        raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
    display = format_atomic(atomic)
    return LendingIntent(str(profile_id), profiles[str(profile_id)], action, mode, display, atomic)


class AavePreflightRpc(Protocol):
    def begin(self) -> tuple[int, int, int]: ...
    def has_code(self, address: str, block: int) -> bool: ...
    def resolve_pool(self, provider: str, block: int) -> str: ...
    def token_decimals(self, token: str, block: int) -> int: ...
    def reserve_a_token(self, data_provider: str, asset: str, block: int) -> str: ...
    def reserve_configuration(self, data_provider: str, asset: str, block: int) -> tuple[int, bool, bool]: ...
    def reserve_caps(self, data_provider: str, asset: str, block: int) -> tuple[int, int]: ...
    def reserve_paused(self, data_provider: str, asset: str, block: int) -> bool: ...
    def reserve_total_supply(self, data_provider: str, asset: str, block: int) -> int: ...
    def account_debt(self, pool: str, account: str, block: int) -> int: ...
    def token_balance(self, token: str, account: str, block: int) -> int: ...
    def allowance(self, token: str, owner: str, spender: str, block: int) -> int: ...
    def pending_nonce(self, account: str) -> int: ...
    def native_balance(self, account: str, block: int) -> int: ...
    def priority_fee(self) -> int: ...
    def estimate_gas(self, transaction: Mapping[str, object]) -> int: ...
    def simulate(self, transaction: Mapping[str, object]) -> bytes: ...
    def l1_fee_upper_bound(self, transaction_size: int, block: int) -> int: ...
    def protocol_asset(self, target: str, block: int) -> str: ...
    def protocol_paused(self, target: str, action: str, block: int) -> bool: ...
    def compound_borrow_balance(self, target: str, account: str, block: int) -> int: ...
    def vault_limit(self, target: str, method: str, account: str, block: int) -> int: ...
    def vault_convert(self, target: str, method: str, amount: int, block: int) -> int: ...


class Web3AavePreflightRpc:
    def __init__(self, endpoint: str, timeout: float = 7.0) -> None:
        provider = Web3.HTTPProvider(
            endpoint, request_kwargs={"timeout": timeout},
            exception_retry_configuration=None,
        )
        self.web3 = Web3(provider)

    def _contract(self, address: str, abi: list[dict[str, Any]]):
        return self.web3.eth.contract(address=address, abi=abi)

    def begin(self) -> tuple[int, int, int]:
        if int(self.web3.eth.chain_id) != 8453:
            raise LendingPreflightError(LendingPreflightCode.WRONG_CHAIN)
        block = self.web3.eth.get_block("latest")
        return int(block["number"]), int(block["timestamp"]), int(block["baseFeePerGas"])

    def has_code(self, address: str, block: int) -> bool:
        return bool(self.web3.eth.get_code(address, block_identifier=block))

    def resolve_pool(self, provider: str, block: int) -> str:
        return Web3.to_checksum_address(
            self._contract(provider, ADDRESS_ABI("getPool")).functions.getPool().call(block_identifier=block),
        )

    def token_decimals(self, token: str, block: int) -> int:
        return int(self._contract(token, UINT_ABI("decimals")).functions.decimals().call(block_identifier=block))

    def reserve_a_token(self, data_provider: str, asset: str, block: int) -> str:
        value = self._contract(data_provider, RESERVE_TOKENS_ABI).functions.getReserveTokensAddresses(asset).call(block_identifier=block)
        return Web3.to_checksum_address(value[0])

    def reserve_configuration(self, data_provider: str, asset: str, block: int) -> tuple[int, bool, bool]:
        value = self._contract(data_provider, RESERVE_CONFIG_ABI).functions.getReserveConfigurationData(asset).call(block_identifier=block)
        return int(value[0]), bool(value[8]), bool(value[9])

    def reserve_caps(self, data_provider: str, asset: str, block: int) -> tuple[int, int]:
        value = self._contract(data_provider, RESERVE_CAPS_ABI).functions.getReserveCaps(asset).call(block_identifier=block)
        return int(value[0]), int(value[1])

    def reserve_paused(self, data_provider: str, asset: str, block: int) -> bool:
        return bool(self._contract(data_provider, PAUSED_ABI).functions.getPaused(asset).call(block_identifier=block))

    def reserve_total_supply(self, data_provider: str, asset: str, block: int) -> int:
        value = self._contract(data_provider, RESERVE_DATA_ABI).functions.getReserveData(asset).call(block_identifier=block)
        return int(value[2])

    def account_debt(self, pool: str, account: str, block: int) -> int:
        value = self._contract(pool, ACCOUNT_DATA_ABI).functions.getUserAccountData(account).call(block_identifier=block)
        return int(value[1])

    def token_balance(self, token: str, account: str, block: int) -> int:
        return int(self._contract(token, BALANCE_ABI).functions.balanceOf(account).call(block_identifier=block))

    def allowance(self, token: str, owner: str, spender: str, block: int) -> int:
        return int(self._contract(token, ALLOWANCE_ABI).functions.allowance(owner, spender).call(block_identifier=block))

    def pending_nonce(self, account: str) -> int:
        return int(self.web3.eth.get_transaction_count(account, "pending"))

    def native_balance(self, account: str, block: int) -> int:
        return int(self.web3.eth.get_balance(account, block_identifier=block))

    def priority_fee(self) -> int:
        return int(self.web3.eth.max_priority_fee)

    def estimate_gas(self, transaction: Mapping[str, object]) -> int:
        return int(self.web3.eth.estimate_gas(dict(transaction)))

    def simulate(self, transaction: Mapping[str, object]) -> bytes:
        return bytes(self.web3.eth.call(dict(transaction)))

    def l1_fee_upper_bound(self, transaction_size: int, block: int) -> int:
        contract = self._contract(BASE_GAS_PRICE_ORACLE, L1_FEE_UPPER_BOUND_ABI)
        return int(contract.functions.getL1FeeUpperBound(
            transaction_size,
        ).call(block_identifier=block))

    def protocol_asset(self, target: str, block: int) -> str:
        for name in ("baseToken", "asset"):
            try:
                return Web3.to_checksum_address(
                    getattr(self._contract(target, ADDRESS_ABI(name)).functions, name)().call(
                        block_identifier=block,
                    ),
                )
            except (BadFunctionCallOutput, ContractLogicError):
                continue
        raise LendingPreflightError("LENDING_IDENTITY_MISMATCH")

    def protocol_paused(self, target: str, action: str, block: int) -> bool:
        name = "isSupplyPaused" if action == "supply" else "isWithdrawPaused"
        return bool(getattr(self._contract(target, BOOL_ABI(name)).functions, name)().call(
            block_identifier=block,
        ))

    def compound_borrow_balance(self, target: str, account: str, block: int) -> int:
        name = "borrowBalanceOf"
        return int(getattr(
            self._contract(target, ACCOUNT_UINT_ABI(name)).functions, name,
        )(account).call(block_identifier=block))

    def vault_limit(self, target: str, method: str, account: str, block: int) -> int:
        return int(getattr(
            self._contract(target, ACCOUNT_UINT_ABI(method)).functions, method,
        )(account).call(block_identifier=block))

    def vault_convert(self, target: str, method: str, amount: int, block: int) -> int:
        return int(getattr(
            self._contract(target, VAULT_UINT_ABI(method)).functions, method,
        )(amount).call(block_identifier=block))


RpcFactory = Callable[[], AavePreflightRpc]


class LendingPreflightService:
    def __init__(
        self, profiles: ActionProfilesState | None = None,
        rpc_factory: RpcFactory | None = None,
        clock: Callable[[], datetime] | None = None,
        environ: Mapping[str, str] | None = None,
        rpc_fallback_factories: tuple[RpcFactory, ...] = (),
    ) -> None:
        self.profiles = profiles or ActionProfilesState.load()
        self._environ = os.environ if environ is None else environ
        self._rpc_factories = (
            (rpc_factory, *rpc_fallback_factories)
            if rpc_factory is not None
            else self._default_rpc_factories()
        )
        self._clock = clock or (lambda: datetime.now(UTC))

    def prepare(
        self, raw_intent: Mapping[str, object], account: Mapping[str, object],
        *, expected_profile_digest: str, frozen_amount_atomic: int | None = None,
    ) -> dict[str, object]:
        if self.profiles.profile is None:
            raise LendingPreflightError(self.profiles.error_code or LendingPreflightCode.PROFILE_UNAVAILABLE.value)
        intent = parse_lending_intent(raw_intent)
        profile = self.profiles.select(intent.profile_id)
        if profile is None:
            raise LendingPreflightError(LendingPreflightCode.PROFILE_UNAVAILABLE)
        if expected_profile_digest != profile.digest:
            raise LendingPreflightError(LendingPreflightCode.PROFILE_MISMATCH)
        label, sender = account.get("label"), account.get("address")
        if (
            not isinstance(label, str) or not label or len(label) > 64
            or not isinstance(sender, str) or not Web3.is_checksum_address(sender)
        ):
            raise LendingPreflightError(LendingPreflightCode.ACCOUNT_UNAVAILABLE)
        for index, factory in enumerate(self._rpc_factories):
            try:
                rpc = factory()
                if isinstance(profile, AaveActionProfile):
                    return self._prepare(
                        profile, intent, label, sender, rpc,
                        frozen_amount_atomic=frozen_amount_atomic,
                    )
                return self._prepare_protocol(
                    profile, intent, label, sender, rpc,
                    frozen_amount_atomic=frozen_amount_atomic,
                )
            except LendingPreflightError as exc:
                if (
                    exc.code == LendingPreflightCode.RPC_UNAVAILABLE.value
                    and index + 1 < len(self._rpc_factories)
                ):
                    continue
                raise
            except Exception as exc:
                if index + 1 < len(self._rpc_factories):
                    continue
                raise LendingPreflightError(LendingPreflightCode.RPC_UNAVAILABLE) from exc
        raise LendingPreflightError(LendingPreflightCode.RPC_UNAVAILABLE)

    def _prepare(
        self, profile: AaveActionProfile, intent: LendingIntent,
        label: str, sender: str, rpc: AavePreflightRpc,
        *, frozen_amount_atomic: int | None = None,
    ) -> dict[str, object]:
        block, block_time, base_fee = rpc.begin()
        now = self._clock().astimezone(UTC).replace(microsecond=0)
        age = int(now.timestamp()) - block_time
        if block <= 0 or age < -FUTURE_TOLERANCE_SECONDS or age > BLOCK_MAX_AGE_SECONDS:
            raise LendingPreflightError(LendingPreflightCode.STALE_BLOCK)
        for address in (profile.asset, profile.pool, profile.provider, profile.data_provider, profile.a_token):
            if not rpc.has_code(address, block):
                raise LendingPreflightError(LendingPreflightCode.IDENTITY_MISMATCH)
        if (
            rpc.resolve_pool(profile.provider, block) != profile.pool
            or rpc.token_decimals(profile.asset, block) != profile.decimals
            or rpc.reserve_a_token(profile.data_provider, profile.asset, block) != profile.a_token
        ):
            raise LendingPreflightError(LendingPreflightCode.IDENTITY_MISMATCH)
        decimals, active, frozen = rpc.reserve_configuration(profile.data_provider, profile.asset, block)
        if decimals != profile.decimals:
            raise LendingPreflightError(LendingPreflightCode.IDENTITY_MISMATCH)
        if not active:
            raise LendingPreflightError(LendingPreflightCode.RESERVE_INACTIVE)
        if frozen:
            raise LendingPreflightError(LendingPreflightCode.RESERVE_FROZEN)
        if rpc.reserve_paused(profile.data_provider, profile.asset, block):
            raise LendingPreflightError(LendingPreflightCode.RESERVE_PAUSED)
        if rpc.account_debt(profile.pool, sender, block) != 0:
            raise LendingPreflightError(LendingPreflightCode.ACCOUNT_HAS_DEBT)
        checks = ["ACTION_PROFILE_VERIFIED", "AAVE_IDENTITY_VERIFIED", "AAVE_RESERVE_AVAILABLE", "AAVE_ACCOUNT_DEBT_ZERO"]
        call_amount = intent.amount_atomic
        expected_amount = intent.amount_atomic
        position_before = 0
        if intent.action == "supply":
            position_before = rpc.token_balance(profile.a_token, sender, block)
            balance = rpc.token_balance(profile.asset, sender, block)
            if intent.amount_mode == "all":
                expected_amount = (
                    frozen_amount_atomic if frozen_amount_atomic is not None else balance
                )
                call_amount = expected_amount
            if call_amount is None or call_amount <= 0 or balance < call_amount:
                raise LendingPreflightError(LendingPreflightCode.INSUFFICIENT_USDC)
            _borrow_cap, supply_cap = rpc.reserve_caps(profile.data_provider, profile.asset, block)
            total_supply = rpc.reserve_total_supply(profile.data_provider, profile.asset, block)
            if supply_cap and total_supply + call_amount > supply_cap * 10**profile.decimals:
                raise LendingPreflightError(LendingPreflightCode.SUPPLY_CAP_REACHED)
            allowance = rpc.allowance(profile.asset, sender, profile.pool, block)
            if allowance == 0:
                next_action, target = "approve", profile.asset
                calldata = encode_approve(profile.pool, call_amount)
                checks.extend(["USDC_BALANCE_AVAILABLE", "AAVE_SUPPLY_CAP_AVAILABLE", "ALLOWANCE_ZERO"])
            elif allowance == call_amount:
                next_action, target = "supply", profile.pool
                calldata = encode_supply(profile.asset, call_amount, sender)
                checks.extend(["USDC_BALANCE_AVAILABLE", "AAVE_SUPPLY_CAP_AVAILABLE", "ALLOWANCE_EXACT"])
            else:
                raise LendingPreflightError(LendingPreflightCode.UNEXPECTED_ALLOWANCE)
        else:
            position = rpc.token_balance(profile.a_token, sender, block)
            expected_amount = position if intent.amount_mode == "all" else intent.amount_atomic
            if expected_amount is None or expected_amount <= 0 or position < expected_amount:
                raise LendingPreflightError(LendingPreflightCode.INSUFFICIENT_POSITION)
            if rpc.token_balance(profile.asset, profile.a_token, block) < expected_amount:
                raise LendingPreflightError(LendingPreflightCode.INSUFFICIENT_LIQUIDITY)
            call_amount = MAX_UINT256 if intent.amount_mode == "all" else expected_amount
            next_action, target = "withdraw", profile.pool
            calldata = encode_withdraw(profile.asset, call_amount, sender)
            checks.extend(["AUSDC_POSITION_AVAILABLE", "AAVE_LIQUIDITY_AVAILABLE"])
        nonce = rpc.pending_nonce(sender)
        priority_fee = rpc.priority_fee()
        max_fee = 2 * base_fee + priority_fee
        transaction: dict[str, object] = {
            "from": sender, "to": target, "value": 0, "data": calldata,
            "nonce": nonce, "type": 2, "chainId": profile.chain_id,
            "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority_fee,
        }
        try:
            gas = gas_with_headroom(rpc.estimate_gas(transaction))
        except Exception as exc:
            raise LendingPreflightError(LendingPreflightCode.GAS_ESTIMATE_FAILED) from exc
        if gas <= 0 or max_fee <= 0:
            raise LendingPreflightError(LendingPreflightCode.GAS_ESTIMATE_FAILED)
        l2_fee_ceiling = gas * max_fee
        try:
            l1_fee_upper_bound = rpc.l1_fee_upper_bound(
                LENDING_TRANSACTION_SIZE_UPPER_BOUND, block,
            )
        except Exception as exc:
            raise LendingPreflightError(LendingPreflightCode.GAS_ESTIMATE_FAILED) from exc
        if l1_fee_upper_bound <= 0:
            raise LendingPreflightError(LendingPreflightCode.GAS_ESTIMATE_FAILED)
        max_total_fee = l2_fee_ceiling + l1_fee_upper_bound
        if rpc.native_balance(sender, block) < max_total_fee:
            raise LendingPreflightError(LendingPreflightCode.INSUFFICIENT_ETH)
        transaction["gas"] = gas
        try:
            rpc.simulate(transaction)
        except Exception as exc:
            raise LendingPreflightError(LendingPreflightCode.SIMULATION_FAILED) from exc
        checks.extend(["FEE_BALANCE_AVAILABLE", "SIMULATION_SUCCEEDED"])
        expires = now + PREVIEW_LIFETIME
        material = {
            "schema_version": "1", "profile_id": profile.profile_id,
            "profile_version": profile.profile_version, "profile_digest": profile.digest,
            "account": sender, "action": intent.action, "next_action": next_action,
            "amount_mode": intent.amount_mode, "amount_atomic": str(expected_amount),
            "call_amount_atomic": str(call_amount), "target": target,
            "calldata_hash": calldata_hash(calldata), "native_value_wei": "0",
            "nonce": str(nonce), "gas": str(gas), "max_fee_per_gas_wei": str(max_fee),
            "max_priority_fee_per_gas_wei": str(priority_fee),
            "l2_fee_ceiling_wei": str(l2_fee_ceiling),
            "l1_fee_upper_bound_wei": str(l1_fee_upper_bound),
            "max_total_fee_wei": str(max_total_fee), "block_number": str(block),
            "observed_at": timestamp(datetime.fromtimestamp(block_time, UTC)),
            "created_at": timestamp(now), "expires_at": timestamp(expires),
        }
        digest = hashlib.sha256(json.dumps(material, separators=(",", ":"), sort_keys=True).encode()).hexdigest()
        return {
            "status": "PREVIEW_READY", "authority_available": False,
            "execution_available": False,
            "account": {"label": label, "address": sender},
            "requested_action": intent.action, "next_action": next_action,
            "protocol": "aave-v3", "profile_id": profile.profile_id,
            "profile_version": profile.profile_version, "profile_digest": profile.digest,
            "network": {"network": "base", "chain_id": profile.chain_id},
            "asset": {"asset": "USDC", "address": profile.asset, "decimals": profile.decimals},
            "amount_mode": intent.amount_mode, "amount_atomic": str(expected_amount),
            "call_amount_atomic": str(call_amount),
            "display_amount": f"{format_atomic(expected_amount)} USDC",
            "target": target, "method": next_action,
            "calldata_hash": material["calldata_hash"], "native_value_wei": "0",
            "nonce": str(nonce), "gas": str(gas),
            "max_fee_per_gas_wei": str(max_fee),
            "max_priority_fee_per_gas_wei": str(priority_fee),
            "l2_fee_ceiling_wei": str(l2_fee_ceiling),
            "l1_fee_upper_bound_wei": str(l1_fee_upper_bound),
            "max_total_fee_wei": str(max_total_fee), "block_number": str(block),
            "observed_at": material["observed_at"], "expires_at": material["expires_at"],
            "preview_digest": digest, "checks": checks,
            "caveats": ["FEE_NOT_POLICY_AUTHORIZED", "PREVIEW_ONLY"],
            "code": "LENDING_ACTION_PREVIEW_READY",
            "message": "Preview ready. Execution requires a new authority action and fresh preflight.",
            "position_before_atomic": str(position_before),
        }

    def _prepare_protocol(
        self, profile: ProtocolActionProfile, intent: LendingIntent,
        label: str, sender: str, rpc: AavePreflightRpc,
        *, frozen_amount_atomic: int | None = None,
    ) -> dict[str, object]:
        block, block_time, base_fee = rpc.begin()
        now = self._clock().astimezone(UTC).replace(microsecond=0)
        age = int(now.timestamp()) - block_time
        if block <= 0 or age < -FUTURE_TOLERANCE_SECONDS or age > BLOCK_MAX_AGE_SECONDS:
            raise LendingPreflightError("LENDING_BLOCK_STALE")
        if (
            not rpc.has_code(profile.asset, block) or not rpc.has_code(profile.target, block)
            or rpc.token_decimals(profile.asset, block) != profile.decimals
            or rpc.protocol_asset(profile.target, block) != profile.asset
        ):
            raise LendingPreflightError("LENDING_IDENTITY_MISMATCH")
        checks = ["ACTION_PROFILE_VERIFIED", "PROTOCOL_IDENTITY_VERIFIED"]
        if profile.protocol_id == "compound-v3":
            if rpc.protocol_paused(profile.target, intent.action, block):
                raise LendingPreflightError("PROTOCOL_ACTION_PAUSED")
            if rpc.compound_borrow_balance(profile.target, sender, block) != 0:
                raise LendingPreflightError("COMPOUND_ACCOUNT_HAS_BORROW")
            checks.extend(["COMPOUND_ACTION_AVAILABLE", "COMPOUND_BORROW_ZERO"])
            position = rpc.token_balance(profile.position_token, sender, block)
        else:
            dead = "0x000000000000000000000000000000000000dEaD"
            if rpc.token_balance(profile.position_token, dead, block) < 10**12:
                raise LendingPreflightError("MORPHO_INFLATION_PROTECTION_MISSING")
            shares = rpc.token_balance(profile.position_token, sender, block)
            position = rpc.vault_convert(
                profile.target, "convertToAssets", shares, block,
            )
            checks.extend(["MORPHO_VAULT_AVAILABLE", "MORPHO_INFLATION_PROTECTION_VERIFIED"])

        expected_amount = intent.amount_atomic
        call_amount = intent.amount_atomic
        if intent.action == "supply":
            balance = rpc.token_balance(profile.asset, sender, block)
            if intent.amount_mode == "all":
                expected_amount = frozen_amount_atomic if frozen_amount_atomic is not None else balance
                call_amount = expected_amount
            if call_amount is None or call_amount <= 0 or balance < call_amount:
                raise LendingPreflightError(LendingPreflightCode.INSUFFICIENT_USDC)
            if profile.protocol_id == "morpho-v1" and rpc.vault_limit(
                profile.target, "maxDeposit", sender, block,
            ) < call_amount:
                raise LendingPreflightError("MORPHO_DEPOSIT_UNAVAILABLE")
            allowance = rpc.allowance(profile.asset, sender, profile.spender, block)
            if allowance == 0:
                next_action, target = "approve", profile.asset
                calldata = encode_approve(profile.spender, call_amount)
                checks.extend(["USDC_BALANCE_AVAILABLE", "ALLOWANCE_ZERO"])
            elif allowance == call_amount:
                target = profile.target
                if profile.protocol_id == "compound-v3":
                    next_action = "supply"
                    calldata = encode_compound_supply(profile.asset, call_amount)
                else:
                    next_action = "deposit"
                    calldata = encode_morpho_deposit(call_amount, sender)
                checks.extend(["USDC_BALANCE_AVAILABLE", "ALLOWANCE_EXACT"])
            else:
                raise LendingPreflightError(LendingPreflightCode.UNEXPECTED_ALLOWANCE)
        else:
            expected_amount = position if intent.amount_mode == "all" else intent.amount_atomic
            if expected_amount is None or expected_amount <= 0 or position < expected_amount:
                raise LendingPreflightError("INSUFFICIENT_LENDING_POSITION")
            target = profile.target
            if profile.protocol_id == "compound-v3":
                liquidity = rpc.token_balance(profile.asset, profile.target, block)
                if liquidity < expected_amount:
                    raise LendingPreflightError(LendingPreflightCode.INSUFFICIENT_LIQUIDITY)
                next_action = "withdraw"
                call_amount = MAX_UINT256 if intent.amount_mode == "all" else expected_amount
                calldata = encode_compound_withdraw(profile.asset, call_amount)
            else:
                available = rpc.vault_limit(profile.target, "maxWithdraw", sender, block)
                if available < expected_amount:
                    raise LendingPreflightError(LendingPreflightCode.INSUFFICIENT_LIQUIDITY)
                if intent.amount_mode == "all":
                    shares = rpc.token_balance(profile.position_token, sender, block)
                    if shares <= 0:
                        raise LendingPreflightError("INSUFFICIENT_LENDING_POSITION")
                    next_action, call_amount = "redeem", shares
                    calldata = encode_morpho_redeem(shares, sender)
                else:
                    next_action, call_amount = "withdraw", expected_amount
                    calldata = encode_morpho_withdraw(expected_amount, sender)
            checks.append("LENDING_POSITION_AVAILABLE")

        nonce = rpc.pending_nonce(sender)
        priority_fee = rpc.priority_fee()
        max_fee = 2 * base_fee + priority_fee
        transaction: dict[str, object] = {
            "from": sender, "to": target, "value": 0, "data": calldata,
            "nonce": nonce, "type": 2, "chainId": profile.chain_id,
            "maxFeePerGas": max_fee, "maxPriorityFeePerGas": priority_fee,
        }
        try:
            gas = gas_with_headroom(rpc.estimate_gas(transaction))
            l1_fee = rpc.l1_fee_upper_bound(LENDING_TRANSACTION_SIZE_UPPER_BOUND, block)
        except Exception as exc:
            raise LendingPreflightError(LendingPreflightCode.GAS_ESTIMATE_FAILED) from exc
        if gas <= 0 or max_fee <= 0 or l1_fee <= 0:
            raise LendingPreflightError(LendingPreflightCode.GAS_ESTIMATE_FAILED)
        l2_fee = gas * max_fee
        total_fee = l2_fee + l1_fee
        if rpc.native_balance(sender, block) < total_fee:
            raise LendingPreflightError(LendingPreflightCode.INSUFFICIENT_ETH)
        transaction["gas"] = gas
        try:
            rpc.simulate(transaction)
        except Exception as exc:
            raise LendingPreflightError(LendingPreflightCode.SIMULATION_FAILED) from exc
        checks.extend(["FEE_BALANCE_AVAILABLE", "SIMULATION_SUCCEEDED"])
        expires = now + PREVIEW_LIFETIME
        material = {
            "schema_version": "2", "profile_id": profile.profile_id,
            "profile_version": profile.profile_version, "profile_digest": profile.digest,
            "account": sender, "action": intent.action, "next_action": next_action,
            "amount_mode": intent.amount_mode, "amount_atomic": str(expected_amount),
            "call_amount_atomic": str(call_amount), "target": target,
            "calldata_hash": calldata_hash(calldata), "native_value_wei": "0",
            "nonce": str(nonce), "gas": str(gas), "max_fee_per_gas_wei": str(max_fee),
            "max_priority_fee_per_gas_wei": str(priority_fee),
            "l2_fee_ceiling_wei": str(l2_fee), "l1_fee_upper_bound_wei": str(l1_fee),
            "max_total_fee_wei": str(total_fee), "block_number": str(block),
            "observed_at": timestamp(datetime.fromtimestamp(block_time, UTC)),
            "created_at": timestamp(now), "expires_at": timestamp(expires),
        }
        digest = hashlib.sha256(
            json.dumps(material, separators=(",", ":"), sort_keys=True).encode(),
        ).hexdigest()
        return {
            "status": "PREVIEW_READY", "authority_available": False,
            "execution_available": False,
            "account": {"label": label, "address": sender},
            "requested_action": intent.action, "next_action": next_action,
            "protocol": profile.protocol_id, "profile_id": profile.profile_id,
            "profile_version": profile.profile_version, "profile_digest": profile.digest,
            "network": {"network": "base", "chain_id": profile.chain_id},
            "asset": {"asset": "USDC", "address": profile.asset, "decimals": 6},
            "amount_mode": intent.amount_mode, "amount_atomic": str(expected_amount),
            "call_amount_atomic": str(call_amount),
            "display_amount": f"{format_atomic(expected_amount)} USDC",
            "target": target, "method": next_action,
            "calldata_hash": material["calldata_hash"], "native_value_wei": "0",
            "nonce": str(nonce), "gas": str(gas),
            "max_fee_per_gas_wei": str(max_fee),
            "max_priority_fee_per_gas_wei": str(priority_fee),
            "l2_fee_ceiling_wei": str(l2_fee), "l1_fee_upper_bound_wei": str(l1_fee),
            "max_total_fee_wei": str(total_fee), "block_number": str(block),
            "observed_at": material["observed_at"], "expires_at": material["expires_at"],
            "preview_digest": digest, "checks": checks,
            "caveats": ["FEE_NOT_POLICY_AUTHORIZED", "PREVIEW_ONLY"],
            "code": "LENDING_ACTION_PREVIEW_READY",
            "message": "Preview ready. Execution requires a new authority action and fresh preflight.",
            "position_before_atomic": str(position),
        }

    def _default_rpc_factories(self) -> tuple[RpcFactory, ...]:
        configured = self._environ.get(BASE_RPC_ENV, "").strip()
        endpoints = (
            ((configured,) if configured else ())
            + (DEFAULT_BASE_RPC_URL, ALCHEMY_BASE_RPC_URL)
        )
        unique: list[str] = []
        for endpoint in endpoints:
            if endpoint and endpoint not in unique:
                unique.append(endpoint)
        return tuple(
            lambda endpoint=endpoint: Web3AavePreflightRpc(endpoint)
            for endpoint in unique
        )


def encode_approve(spender: str, amount: int) -> str:
    return "0x095ea7b3" + _address_word(spender) + _uint_word(amount)


def encode_supply(asset: str, amount: int, beneficiary: str) -> str:
    return "0x617ba037" + _address_word(asset) + _uint_word(amount) + _address_word(beneficiary) + _uint_word(0)


def encode_withdraw(asset: str, amount: int, beneficiary: str) -> str:
    return "0x69328dec" + _address_word(asset) + _uint_word(amount) + _address_word(beneficiary)


def encode_compound_supply(asset: str, amount: int) -> str:
    return "0xf2b9fdb8" + _address_word(asset) + _uint_word(amount)


def encode_compound_withdraw(asset: str, amount: int) -> str:
    return "0xf3fef3a3" + _address_word(asset) + _uint_word(amount)


def encode_morpho_deposit(amount: int, receiver: str) -> str:
    return "0x6e553f65" + _uint_word(amount) + _address_word(receiver)


def encode_morpho_withdraw(amount: int, account: str) -> str:
    return "0xb460af94" + _uint_word(amount) + _address_word(account) + _address_word(account)


def encode_morpho_redeem(shares: int, account: str) -> str:
    return "0xba087652" + _uint_word(shares) + _address_word(account) + _address_word(account)


def _address_word(value: str) -> str:
    if not Web3.is_checksum_address(value):
        raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
    return value[2:].lower().rjust(64, "0")


def _uint_word(value: int) -> str:
    if type(value) is not int or value < 0 or value > MAX_UINT256:
        raise LendingPreflightError(LendingPreflightCode.REQUEST_INVALID)
    return f"{value:064x}"


def calldata_hash(value: str) -> str:
    return hashlib.sha256(bytes.fromhex(value[2:])).hexdigest()


def format_atomic(value: int) -> str:
    whole, fraction = divmod(value, 10**6)
    suffix = f".{fraction:06d}".rstrip("0").rstrip(".")
    return f"{whole}{suffix}"


def timestamp(value: datetime) -> str:
    return value.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_UNAVAILABLE_CODES = frozenset({
    "ACTION_PROFILES_CORRUPT", "ACTION_PROFILES_INCOMPATIBLE",
    "ACTION_PROFILES_INTEGRITY_FAILED", "ACTION_PROFILES_MISSING",
    "ACTION_PROFILES_UNAVAILABLE", "ACTION_PROFILE_MISMATCH", "ACCOUNT_CHANGED", "AAVE_BLOCK_STALE",
    "AAVE_IDENTITY_MISMATCH", "LENDING_BLOCK_STALE", "LENDING_IDENTITY_MISMATCH",
    "BASE_RPC_UNAVAILABLE", "WALLET_ACCOUNT_UNAVAILABLE",
    "WALLET_UNAVAILABLE", "WRONG_CHAIN",
})


def unavailable_preview(
    reason: str, *, requested_action: str | None = None,
    amount_mode: str | None = None, account: Mapping[str, object] | None = None,
    profile_digest: str | None = None,
    profile_id: str = "aave-v3-base-usdc",
) -> dict[str, object]:
    status = "UNAVAILABLE" if reason in _UNAVAILABLE_CODES else "REFUSED"
    protocol = {
        "aave-v3-base-usdc": "aave-v3",
        "compound-v3-base-usdc": "compound-v3",
        "morpho-v1-gauntlet-usdc-prime": "morpho-v1",
    }.get(profile_id, "aave-v3")
    return {
        "status": status, "authority_available": False,
        "execution_available": False, "account": None if account is None else dict(account),
        "requested_action": requested_action, "next_action": None,
        "protocol": protocol, "profile_id": profile_id,
        "profile_version": "1", "profile_digest": profile_digest,
        "network": {"network": "base", "chain_id": 8453},
        "asset": {"asset": "USDC", "address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "decimals": 6},
        "amount_mode": amount_mode, "amount_atomic": None, "call_amount_atomic": None,
        "display_amount": None,
        "target": None, "method": None, "calldata_hash": None,
        "native_value_wei": "0", "nonce": None, "gas": None,
        "max_fee_per_gas_wei": None, "max_priority_fee_per_gas_wei": None,
        "l2_fee_ceiling_wei": None, "l1_fee_upper_bound_wei": None,
        "max_total_fee_wei": None, "block_number": None, "observed_at": None,
        "expires_at": None, "preview_digest": None, "checks": [],
        "position_before_atomic": None,
        "caveats": [reason],
        "code": "LENDING_ACTION_UNAVAILABLE" if status == "UNAVAILABLE" else "LENDING_ACTION_REFUSED",
        "message": (
            "Lending action preview is unavailable."
            if status == "UNAVAILABLE" else "Lending action preview was refused."
        ),
    }
