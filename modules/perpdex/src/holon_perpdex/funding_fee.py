"""Build the fixed, reviewed EIP-1559 envelope for one funding transfer."""

from __future__ import annotations

from dataclasses import replace

from holon_wallet.transfer import PreparedTransferAction


def bounded_funding_action(
    action: PreparedTransferAction, fee_ceiling_wei: int,
) -> PreparedTransferAction:
    """Keep the final unsigned action inside Guard's explicit fee ceiling."""
    transaction = action.transaction
    if fee_ceiling_wei <= 0 or transaction.gas <= 0 or transaction.max_fee_per_gas <= 0:
        raise ValueError("Invalid funding fee ceiling")
    reserve_gas = max(1, transaction.gas // 10)
    gas = min(transaction.gas + reserve_gas, fee_ceiling_wei // transaction.max_fee_per_gas)
    max_fee = fee_ceiling_wei // gas if gas else 0
    if gas < transaction.gas or max_fee < transaction.max_fee_per_gas:
        raise ValueError("Funding fee ceiling is below the live quote")
    bounded = replace(
        transaction,
        gas=gas,
        max_fee_per_gas=max_fee,
        max_priority_fee_per_gas=max_fee,
    )
    return replace(action, transaction=bounded, max_total_fee_wei=gas * max_fee)
