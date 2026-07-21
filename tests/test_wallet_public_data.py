from __future__ import annotations

from dataclasses import replace

from holon_wallet.public_data import (
    BASE_USDC,
    ETHEREUM_USDC,
    AssetBalance,
    NetworkSnapshot,
    PublicDataService,
    PublicDataStatus,
    format_units,
    snapshot_to_map,
)


ADDRESS = "0x" + "12" * 20


class FakeRpc:
    def __init__(
        self,
        chain_id: int,
        *,
        block: int = 10,
        native: int = 0,
        usdc: int = 0,
        decimals: int = 6,
        failure: Exception | None = None,
    ) -> None:
        self.expected_chain_id = chain_id
        self.block = block
        self.native = native
        self.usdc = usdc
        self.decimals = decimals
        self.failure = failure
        self.contracts: list[str] = []

    def chain_id(self) -> int:
        if self.failure is not None:
            raise self.failure
        return self.expected_chain_id

    def block_number(self) -> int:
        return self.block

    def native_balance(self, address: str) -> int:
        assert address == ADDRESS
        return self.native

    def token_decimals(self, contract: str) -> int:
        self.contracts.append(contract)
        return self.decimals

    def token_balance(self, contract: str, address: str) -> int:
        self.contracts.append(contract)
        assert address == ADDRESS
        return self.usdc


def test_reads_both_allowlisted_networks_and_preserves_real_zero() -> None:
    clients = {
        "ethereum": FakeRpc(1, native=0, usdc=1_250_000),
        "base": FakeRpc(8453, native=2 * 10**18, usdc=0),
    }
    endpoints: list[tuple[str, str]] = []

    def factory(network_id: str, endpoint: str) -> FakeRpc:
        endpoints.append((network_id, endpoint))
        return clients[network_id]

    result = PublicDataService(factory, {}).refresh("profile-1", ADDRESS)

    assert result.profile_id == "profile-1"
    ethereum, base = result.networks
    assert ethereum.status is PublicDataStatus.LIVE
    assert ethereum.eth == AssetBalance("ETH", 0, 18)
    assert ethereum.eth.display_value == "0 ETH"
    assert ethereum.usdc.display_value == "1.25 USDC"
    assert base.status is PublicDataStatus.LIVE
    assert base.eth.display_value == "2 ETH"
    assert base.usdc.display_value == "0 USDC"
    assert clients["ethereum"].contracts == [ETHEREUM_USDC, ETHEREUM_USDC]
    assert clients["base"].contracts == [BASE_USDC, BASE_USDC]
    assert endpoints == [
        ("ethereum", "https://ethereum-rpc.publicnode.com"),
        ("base", "https://base-rpc.publicnode.com"),
    ]


def test_wrong_chain_and_invalid_token_metadata_are_unavailable() -> None:
    clients = {
        "ethereum": FakeRpc(8453),
        "base": FakeRpc(8453, decimals=18),
    }
    service = PublicDataService(lambda network, _endpoint: clients[network], {})

    result = service.refresh("profile-1", ADDRESS)

    assert result.networks[0].status is PublicDataStatus.UNAVAILABLE
    assert result.networks[0].error_code == "WRONG_CHAIN"
    assert result.networks[0].eth is None
    assert result.networks[1].error_code == "TOKEN_METADATA_INVALID"


def test_timeout_retries_once_and_endpoint_override_is_not_exposed() -> None:
    calls = 0

    def factory(_network: str, endpoint: str) -> FakeRpc:
        nonlocal calls
        calls += 1
        assert endpoint == "https://token-value.example/rpc"
        return FakeRpc(1, failure=TimeoutError())

    service = PublicDataService(
        factory, {"HOLON_ETHEREUM_RPC_URL": "https://token-value.example/rpc"},
    )
    snapshot = service.refresh("profile-1", ADDRESS, ("ethereum",)).networks[0]

    assert calls == 2
    assert snapshot.error_code == "RPC_UNAVAILABLE"
    assert "token-value" not in repr(snapshot)
    assert "token-value" not in repr(snapshot_to_map(snapshot))


def test_partial_results_and_simulated_label_stay_distinct() -> None:
    clients = {
        "ethereum": FakeRpc(1, native=10**18),
        "base": FakeRpc(8453, failure=RuntimeError("offline")),
    }
    result = PublicDataService(
        lambda network, _endpoint: clients[network], {},
    ).refresh("profile-1", ADDRESS)

    assert [item.status for item in result.networks] == [
        PublicDataStatus.LIVE,
        PublicDataStatus.UNAVAILABLE,
    ]
    simulated = replace(result.networks[0], status=PublicDataStatus.SIMULATED)
    assert snapshot_to_map(simulated)["status"] == "SIMULATED"


def test_formatting_never_turns_small_nonzero_value_into_zero() -> None:
    assert format_units(1, 18, "ETH") == "<0.000001 ETH"
    assert format_units(1_234_567_890_000_000_000, 18, "ETH") == "1.234567 ETH"
    assert format_units(1_000_001, 6, "USDC") == "1.000001 USDC"


def test_unknown_network_is_refused_before_provider_use() -> None:
    service = PublicDataService(lambda *_args: (_ for _ in ()).throw(AssertionError()), {})
    try:
        service.refresh("profile-1", ADDRESS, ("arbitrum",))
    except ValueError as error:
        assert str(error) == "Unsupported public-data network"
    else:
        raise AssertionError("Unknown network was accepted")
