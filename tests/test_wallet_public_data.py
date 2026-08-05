from __future__ import annotations

from dataclasses import replace

from holon_wallet.public_data import (
    BASE_USDC,
    ETHEREUM_USDC,
    AssetBalance,
    AssetReadError,
    NetworkSnapshot,
    NETWORK_BY_ID,
    PublicDataService,
    PublicDataStatus,
    Web3PublicRpc,
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
        "arbitrum": FakeRpc(42161),
        "optimism": FakeRpc(10),
        "polygon": FakeRpc(137),
        "bsc": FakeRpc(56),
    }
    endpoints: list[tuple[str, str]] = []

    def factory(network_id: str, endpoint: str) -> FakeRpc:
        endpoints.append((network_id, endpoint))
        return clients[network_id]

    result = PublicDataService(factory, {}).refresh("profile-1", ADDRESS)

    assert result.profile_id == "profile-1"
    ethereum, base, arbitrum, optimism, polygon, bsc = result.networks
    assert ethereum.status is PublicDataStatus.LIVE
    assert ethereum.eth == AssetBalance("ETH", 0, 18)
    assert ethereum.eth.display_value == "0 ETH"
    assert ethereum.usdc.display_value == "1.25 USDC"
    assert base.status is PublicDataStatus.LIVE
    assert base.eth.display_value == "2 ETH"
    assert base.usdc.display_value == "0 USDC"
    assert arbitrum.status is PublicDataStatus.LIVE
    assert optimism.status is PublicDataStatus.LIVE
    assert polygon.status is PublicDataStatus.LIVE
    assert bsc.status is PublicDataStatus.LIVE
    assert clients["ethereum"].contracts == [ETHEREUM_USDC, ETHEREUM_USDC]
    assert clients["base"].contracts == [BASE_USDC, BASE_USDC]
    assert set(endpoints) == {
        ("ethereum", "https://ethereum-rpc.publicnode.com"),
        ("base", "https://base-rpc.publicnode.com"),
        ("arbitrum", "https://arb1.arbitrum.io/rpc"),
        ("optimism", "https://mainnet.optimism.io"),
        ("polygon", "https://polygon-bor-rpc.publicnode.com"),
        ("bsc", "https://bsc-dataseed.bnbchain.org"),
    }


def test_six_network_refresh_keeps_parallelism_bounded_to_four(monkeypatch) -> None:
    import concurrent.futures
    import holon_wallet.public_data as module

    observed: list[int] = []
    real_executor = concurrent.futures.ThreadPoolExecutor

    def executor(*, max_workers: int, thread_name_prefix: str):
        observed.append(max_workers)
        return real_executor(
            max_workers=max_workers, thread_name_prefix=thread_name_prefix,
        )

    monkeypatch.setattr(module, "ThreadPoolExecutor", executor)
    clients = {
        network_id: FakeRpc(spec.chain_id)
        for network_id, spec in NETWORK_BY_ID.items()
    }
    PublicDataService(
        lambda network_id, _endpoint: clients[network_id], {},
    ).refresh("profile-1", ADDRESS)

    assert observed == [4]


def test_wrong_chain_and_invalid_token_metadata_are_unavailable() -> None:
    clients = {
        "ethereum": FakeRpc(8453),
        "base": FakeRpc(8453, decimals=18),
    }
    service = PublicDataService(lambda network, _endpoint: clients[network], {})

    result = service.refresh("profile-1", ADDRESS, ("ethereum", "base"))

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
    assert snapshot.error_code == "RPC_TIMEOUT"
    assert "token-value" not in repr(snapshot)
    assert "token-value" not in repr(snapshot_to_map(snapshot))


def test_partial_results_and_simulated_label_stay_distinct() -> None:
    clients = {
        "ethereum": FakeRpc(1, native=10**18),
        "base": FakeRpc(8453, failure=RuntimeError("offline")),
    }
    result = PublicDataService(
        lambda network, _endpoint: clients[network], {},
    ).refresh("profile-1", ADDRESS, ("ethereum", "base"))

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
        service.refresh("profile-1", ADDRESS, ("unknown",))
    except ValueError as error:
        assert str(error) == "Unsupported public-data network"
    else:
        raise AssertionError("Unknown network was accepted")


def _abi_symbol(value: str) -> str:
    raw = value.encode("utf-8")
    padded = raw + b"\0" * ((32 - len(raw) % 32) % 32)
    return "0x" + (32).to_bytes(32, "big").hex() + len(raw).to_bytes(32, "big").hex() + padded.hex()


class _Response:
    status_code = 200

    def __init__(self, value) -> None:
        self.value = value

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self.value


def test_batch_is_pinned_to_block_and_missing_item_uses_one_fallback(monkeypatch) -> None:
    spec = NETWORK_BY_ID["optimism"]
    assets = {item.asset_id: item for item in spec.assets}
    calls: list[object] = []

    def post(_endpoint, *, json, timeout):
        assert timeout == 5.0
        calls.append(json)
        requests = json if isinstance(json, list) else [json]
        output = []
        for request in requests:
            identity = request["id"]
            if isinstance(json, list) and identity == "op:symbol":
                continue
            asset_id, kind = identity.split(":")
            if request["method"] in {"eth_call", "eth_getBalance"}:
                assert request["params"][-1] == "0x7b"
            value = "0x0"
            if kind == "decimals":
                value = hex(assets[asset_id].decimals)
            elif kind == "symbol":
                value = _abi_symbol(assets[asset_id].onchain_symbols[0])
            output.append({"jsonrpc": "2.0", "id": identity, "result": value})
        return _Response(output if isinstance(json, list) else output[0])

    monkeypatch.setattr("holon_wallet.public_data.requests.post", post)
    balances, errors = Web3PublicRpc("https://rpc.example").asset_balances(
        spec, ADDRESS, 123,
    )

    assert not errors
    assert [item.asset_id for item in balances] == [item.asset_id for item in spec.assets]
    assert len(calls) == 2
    assert isinstance(calls[0], list)
    assert calls[1]["id"] == "op:symbol"


def test_one_token_metadata_error_does_not_discard_other_batch_balances(monkeypatch) -> None:
    spec = NETWORK_BY_ID["optimism"]
    assets = {item.asset_id: item for item in spec.assets}

    def post(_endpoint, *, json, timeout):
        del timeout
        output = []
        for request in json:
            asset_id, kind = request["id"].split(":")
            value = "0x0"
            if kind == "decimals":
                value = hex(assets[asset_id].decimals)
            elif kind == "symbol":
                symbol = "BROKEN" if asset_id == "dai" else assets[asset_id].onchain_symbols[0]
                value = _abi_symbol(symbol)
            output.append({"jsonrpc": "2.0", "id": request["id"], "result": value})
        return _Response(output)

    monkeypatch.setattr("holon_wallet.public_data.requests.post", post)
    balances, errors = Web3PublicRpc("https://rpc.example").asset_balances(
        spec, ADDRESS, 123,
    )

    assert "dai" not in {item.asset_id for item in balances}
    assert errors == (AssetReadError("dai", "TOKEN_METADATA_INVALID"),)
    assert {item.asset_id for item in balances} == {item.asset_id for item in spec.assets} - {"dai"}


def test_json_rpc_rate_limit_retries_whole_network_once(monkeypatch) -> None:
    posts = 0

    def post(_endpoint, *, json, timeout):
        nonlocal posts
        del json, timeout
        posts += 1
        return _Response({
            "jsonrpc": "2.0", "id": None,
            "error": {"code": -32005, "message": "rate limit exceeded"},
        })

    class RateLimitedRpc(FakeRpc):
        def asset_balances(self, spec, address, block):
            return Web3PublicRpc("https://rpc.example").asset_balances(
                spec, address, block,
            )

    monkeypatch.setattr("holon_wallet.public_data.requests.post", post)
    service = PublicDataService(
        lambda _network, _endpoint: RateLimitedRpc(10), {},
    )

    snapshot = service.refresh(
        "profile-1", ADDRESS, ("optimism",),
    ).networks[0]

    assert posts == 2
    assert snapshot.status is PublicDataStatus.UNAVAILABLE
    assert snapshot.error_code == "RATE_LIMITED"
