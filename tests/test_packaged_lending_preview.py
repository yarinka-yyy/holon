from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from eth_abi import encode
from web3 import Web3

from holon_contracts import MessageKind
from holon_guard_ipc import PipeClient
from holon_guard_ipc.client import wait_for_pipe
from holon_guard.action_store import ActionStateStore
from holon_guard.request_store import RequestStateStore
from holon_guard.store import SnapshotStore
from holon_journal import JournalStore
from holon_lending import AAVE_CONTRACTS, BASE_USDC
from holon_wallet_control import WalletControlClient
from holon_wallet_control.lending_operation import LendingOperationStore
from holon_wallet.settings import SettingsStore
from holon_wallet.prices import AssetPrice, PriceSnapshot, PriceStatus
from holon_wallet.public_cache import PublicCacheStore
from holon_wallet.storage import WalletPaths
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic
from wallet_public_support import public_snapshot


def _selector(signature: str) -> str:
    return Web3.keccak(text=signature).hex()[:8]


def _result(types: list[str], values: list[object]) -> str:
    return "0x" + encode(types, values).hex()


class RpcFixture(BaseHTTPRequestHandler):
    calls: list[str] = []

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
        length = int(self.headers.get("Content-Length", "0"))
        request = json.loads(self.rfile.read(length))
        method, params = request["method"], request.get("params", [])
        self.calls.append(method)
        result = self._response(method, params)
        body = json.dumps({"jsonrpc": "2.0", "id": request["id"], "result": result}).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    @staticmethod
    def _response(method: str, params: list[object]) -> object:
        if method == "eth_chainId":
            return hex(8453)
        if method == "eth_getBlockByNumber":
            import time

            return {
                "number": hex(50_000_000), "timestamp": hex(int(time.time()) - 1),
                "baseFeePerGas": hex(10_000_000),
            }
        if method == "eth_getCode":
            return "0x01"
        if method == "eth_getTransactionCount":
            return "0x7"
        if method == "eth_maxPriorityFeePerGas":
            return hex(1_000_000)
        if method == "eth_estimateGas":
            return hex(75_000)
        if method == "eth_getBalance":
            return hex(10**18)
        if method != "eth_call":
            raise AssertionError(f"Unexpected RPC method: {method}")
        transaction = params[0]
        assert isinstance(transaction, dict)
        target = str(transaction.get("to", "")).lower()
        data = str(transaction.get("data", "")).removeprefix("0x")
        if data.startswith(_selector("getPool()")):
            return _result(["address"], [AAVE_CONTRACTS["pool"]])
        if data.startswith(_selector("decimals()")):
            return _result(["uint256"], [6])
        if data.startswith(_selector("getReserveTokensAddresses(address)")):
            return _result(
                ["address", "address", "address"],
                [AAVE_CONTRACTS["a_token"], "0x" + "22" * 20, "0x" + "33" * 20],
            )
        if data.startswith(_selector("getReserveConfigurationData(address)")):
            return _result(
                ["uint256"] * 5 + ["bool"] * 5,
                [6, 0, 0, 0, 0, False, False, False, True, False],
            )
        if data.startswith(_selector("getReserveCaps(address)")):
            return _result(["uint256", "uint256"], [0, 230_000_000])
        if data.startswith(_selector("getPaused(address)")):
            return _result(["bool"], [False])
        if data.startswith(_selector("getReserveData(address)")):
            return _result(["uint256"] * 12, [0, 0, 100_000_000] + [0] * 9)
        if data.startswith(_selector("getUserAccountData(address)")):
            return _result(["uint256"] * 6, [0] * 6)
        if data.startswith(_selector("balanceOf(address)")):
            if target == AAVE_CONTRACTS["a_token"].lower():
                return _result(["uint256"], [999_999])
            return _result(["uint256"], [10_000_000])
        if data.startswith(_selector("allowance(address,address)")):
            return _result(["uint256"], [0])
        if data.startswith(_selector("getL1FeeUpperBound(uint256)")):
            return _result(["uint256"], [20_000])
        if target == BASE_USDC.lower() and data.startswith(_selector("approve(address,uint256)")):
            return _result(["bool"], [True])
        if target == AAVE_CONTRACTS["pool"].lower() and data.startswith(
            _selector("withdraw(address,uint256,address)")
        ):
            return _result(["uint256"], [999_999])
        raise AssertionError(f"Unexpected eth_call: {target} {data[:8]}")


class FundingRpcFixture(RpcFixture):
    snapshots = (50, 51, 51)
    snapshot_index = 0

    @classmethod
    def reset(cls) -> None:
        cls.calls = []
        cls.snapshot_index = 0

    @classmethod
    def _response(cls, method: str, params: list[object]) -> object:
        if method == "eth_chainId":
            return hex(42161)
        if method == "eth_blockNumber":
            return hex(300_000_000)
        if method == "eth_getBlockByNumber":
            index = min(cls.snapshot_index, len(cls.snapshots) - 1)
            cls.snapshot_index += 1
            return {
                "number": hex(300_000_000 + index),
                "timestamp": hex(int(time.time()) - 1),
                "baseFeePerGas": hex(cls.snapshots[index]),
            }
        if method == "eth_maxPriorityFeePerGas":
            return hex(1 if cls.snapshot_index >= 3 else 0)
        if method == "eth_estimateGas":
            return hex(1)
        if method == "eth_sendRawTransaction":
            return "0x" + "ab" * 32
        return super()._response(method, params)


@pytest.mark.skipif(sys.platform != "win32", reason="Packaged Windows executables")
def test_packaged_wallet_boots_offline_with_public_cache(tmp_path: Path) -> None:
    wallet = Path(os.environ.get("HOLON_TEST_WALLET_EXE", ""))
    if not wallet.is_file():
        pytest.skip("Packaged Wallet path was not provided")
    local_root = tmp_path / "cached-local"
    paths = WalletPaths(local_root / "Holon" / "data")
    repository = VaultRepository(paths)
    record = repository.new_record(generate_mnemonic(), "Cached Fixture")
    repository.create_new("fixture-password", record)
    SettingsStore(paths).save_active_id(record.summary.profile_id)
    prices = PriceSnapshot(
        8453,
        PriceStatus.LIVE,
        (
            AssetPrice("eth", "ETH", PriceStatus.LIVE, 250_000_000_000, 8, 10),
            AssetPrice("usdc", "USDC", PriceStatus.LIVE, 100_000_000, 8, 10),
        ),
        10,
    )
    cache = PublicCacheStore(paths)
    cache.save(
        record.summary.profile_id,
        record.summary.address,
        {
            "ethereum": public_snapshot("ethereum", eth=2 * 10**18),
            "base": public_snapshot("base", usdc=7_000_000),
        },
        prices,
    )
    before = cache.path.read_bytes()
    environment = dict(os.environ)
    environment.update({
        "LOCALAPPDATA": str(local_root),
        "HOLON_ETHEREUM_RPC_URL": "http://127.0.0.1:9",
        "HOLON_BASE_RPC_URL": "http://127.0.0.1:9",
        "QT_QPA_PLATFORM": "offscreen",
    })
    process = subprocess.Popen(
        [str(wallet.resolve())],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=environment,
        creationflags=0x08000000,
    )
    try:
        time.sleep(2)
        assert process.poll() is None
        assert cache.path.read_bytes() == before
    finally:
        if process.poll() is None:
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
        process.wait(timeout=10)


def _terminate_test_tree(process: subprocess.Popen[object]) -> None:
    if process.poll() is not None:
        return
    terminated = subprocess.run(
        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
        check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
    )
    if terminated.returncode != 0:
        raise RuntimeError("Test process tree could not be terminated")
    process.wait(timeout=10)


@pytest.mark.skipif(sys.platform != "win32", reason="Packaged Windows executables")
def test_packaged_guard_cold_starts_and_reactivates_wallet(tmp_path: Path) -> None:
    if os.environ.get("HOLON_TEST_PACKAGED_E2E") != "1":
        pytest.skip("Dedicated packaged Wallet E2E was not requested")
    guard = Path(os.environ.get("HOLON_TEST_GUARD_EXE", ""))
    wallet = Path(os.environ.get("HOLON_TEST_WALLET_EXE", ""))
    if not guard.is_file() or not wallet.is_file():
        pytest.skip("Packaged Guard and Wallet paths were not provided")

    local_root = tmp_path / "cold-start-local"
    paths = WalletPaths(local_root / "Holon" / "data")
    repository = VaultRepository(paths)
    record = repository.new_record(generate_mnemonic(), "Cold Start Fixture")
    repository.create_new("fixture-password", record)
    SettingsStore(paths).save_active_id(record.summary.profile_id)
    data_dir = paths.data_dir
    first_pipe = rf"\\.\pipe\Holon.Guard.cold-start.{uuid.uuid4()}"
    second_pipe = rf"\\.\pipe\Holon.Guard.cold-start-second.{uuid.uuid4()}"
    third_pipe = rf"\\.\pipe\Holon.Guard.cold-start-third.{uuid.uuid4()}"
    environment = dict(os.environ)
    environment.update({
        "LOCALAPPDATA": str(local_root),
        "QT_QPA_PLATFORM": "offscreen",
        "HOLON_ETHEREUM_RPC_URL": "http://127.0.0.1:9",
        "HOLON_BASE_RPC_URL": "http://127.0.0.1:9",
    })

    def start(pipe: str) -> subprocess.Popen[object]:
        return subprocess.Popen(
            [
                str(guard.resolve()), "--data-dir", str(data_dir),
                "--pipe-name", pipe, "--wallet-path", str(wallet.resolve()),
                "--wallet-status-pipe-name", f"{pipe}.status",
                "--policy-control-pipe-name", f"{pipe}.policy",
            ],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=environment, creationflags=0x08000000,
        )

    first = start(first_pipe)
    second: subprocess.Popen[object] | None = None
    third: subprocess.Popen[object] | None = None
    wallet_pid: int | None = None
    try:
        wait_for_pipe(first_pipe, 20.0)
        started_at = time.monotonic()
        opened = PipeClient(first_pipe, 2.0, 20.0).request(MessageKind.OPEN_WALLET)
        assert time.monotonic() - started_at < 16.0
        assert opened.kind is MessageKind.WALLET_OPENED
        assert opened.payload["wallet_state"] == "OPENED"
        assert opened.payload["code"] == "WALLET_OPENED"
        wallet_pid = WalletControlClient().activate(
            str(uuid.uuid4()), wallet.resolve(), 2.0,
        )
        activated = PipeClient(first_pipe, 2.0, 20.0).request(MessageKind.OPEN_WALLET)
        assert activated.kind is MessageKind.WALLET_OPENED
        assert activated.payload["wallet_state"] == "ACTIVATED"
        assert activated.payload["code"] == "WALLET_ACTIVATED"
        assert WalletControlClient().activate(
            str(uuid.uuid4()), wallet.resolve(), 2.0,
        ) == wallet_pid
        assert not (data_dir / "action-state.json").exists()

        second = start(second_pipe)
        assert second.wait(timeout=10) == 3
        _terminate_test_tree(first)

        third = start(third_pipe)
        wait_for_pipe(third_pipe, 20.0)
        health = PipeClient(third_pipe, 2.0, 2.0).request(MessageKind.HEALTH_REQUEST)
        assert health.payload["guard_state"] in {"NORMAL", "SIGNING_DISABLED"}
    finally:
        for process in (third, second, first):
            if process is not None:
                _terminate_test_tree(process)
        if wallet_pid is not None:
            subprocess.run(
                ["taskkill", "/PID", str(wallet_pid), "/T", "/F"],
                check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=10,
            )


@pytest.mark.skipif(sys.platform != "win32", reason="Packaged Windows executables")
def test_packaged_guard_wallet_preview_with_offline_rpc(tmp_path: Path) -> None:
    guard = Path(os.environ.get("HOLON_TEST_GUARD_EXE", ""))
    wallet = Path(os.environ.get("HOLON_TEST_WALLET_EXE", ""))
    if not guard.is_file() or not wallet.is_file():
        pytest.skip("Packaged Guard and Wallet paths were not provided")
    local_root = tmp_path / "local"
    paths = WalletPaths(local_root / "Holon" / "data")
    repository = VaultRepository(paths)
    record = repository.new_record(generate_mnemonic(), "Packaged Fixture")
    repository.create_new("fixture-password", record)
    SettingsStore(paths).save_active_id(record.summary.profile_id)

    RpcFixture.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), RpcFixture)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    pipe = rf"\\.\pipe\Holon.Guard.packaged-preview.{uuid.uuid4()}"
    environment = dict(os.environ)
    environment.update({
        "LOCALAPPDATA": str(local_root),
        "HOLON_BASE_RPC_URL": f"http://127.0.0.1:{server.server_port}",
    })
    process = subprocess.Popen(
        [
            str(guard.resolve()), "--data-dir", str(paths.data_dir),
            "--pipe-name", pipe, "--wallet-path", str(wallet.resolve()),
            "--wallet-status-pipe-name", f"{pipe}.status",
            "--policy-control-pipe-name", f"{pipe}.policy",
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=environment, creationflags=0x08000000,
    )
    try:
        wait_for_pipe(pipe, 20.0)
        response = PipeClient(pipe, 2.0, 35.0).request(
            MessageKind.LENDING_ACTION_INTENT,
            {
                "module_id": "lending", "module_version": "1",
                "protocol_profile_id": "aave-v3-base-usdc",
                "protocol_profile_version": "1", "network": "base", "asset": "usdc",
                "beneficiary_mode": "active_wallet_account", "action": "supply",
                "amount_mode": "exact", "amount": "1",
            },
        )
        assert response.kind is MessageKind.LENDING_ACTION_PREVIEW
        assert response.payload["status"] == "PREVIEW_READY", (
            response.payload.get("status"), response.payload.get("reason"),
            response.payload.get("code"), response.payload.get("message"),
            response.payload.get("caveats"),
        )
        assert response.payload["next_action"] == "approve"
        assert response.payload["amount_atomic"] == "1000000"
        assert response.payload["authority_available"] is False
        assert response.payload["execution_available"] is False
        assert "eth_sendRawTransaction" not in RpcFixture.calls
        assert not (paths.data_dir / "action-state.json").exists()
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10,
        )
        process.wait(timeout=10)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


@pytest.mark.skipif(sys.platform != "win32", reason="Packaged Windows executables")
def test_packaged_funding_reaches_review_with_bounded_fee_drift(tmp_path: Path) -> None:
    guard = Path(os.environ.get("HOLON_TEST_GUARD_EXE", ""))
    wallet = Path(os.environ.get("HOLON_TEST_WALLET_EXE", ""))
    if not guard.is_file() or not wallet.is_file():
        pytest.skip("Packaged Guard and Wallet paths were not provided")
    local_root = tmp_path / "funding-local"
    paths = WalletPaths(local_root / "Holon" / "data")
    repository = VaultRepository(paths)
    record = repository.new_record(generate_mnemonic(), "Funding Fixture")
    repository.create_new("fixture-password", record)
    SettingsStore(paths).save_active_id(record.summary.profile_id)
    SnapshotStore(paths.data_dir / "guard-state.json").bootstrap_normal_for_test()
    ActionStateStore(paths.data_dir / "action-state.json").bootstrap_empty_for_test()
    RequestStateStore(paths.data_dir / "request-control-state.json").bootstrap_empty_for_test()
    JournalStore(paths.data_dir / "journal.jsonl").bootstrap_empty_for_test()

    FundingRpcFixture.reset()
    server = ThreadingHTTPServer(("127.0.0.1", 0), FundingRpcFixture)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    pipe = rf"\\.\pipe\Holon.Guard.packaged-funding.{uuid.uuid4()}"
    environment = dict(os.environ)
    environment.update({
        "LOCALAPPDATA": str(local_root),
        "HOLON_ARBITRUM_RPC_URL": f"http://127.0.0.1:{server.server_port}",
        "HOLON_ETHEREUM_RPC_URL": "http://127.0.0.1:9",
        "HOLON_BASE_RPC_URL": "http://127.0.0.1:9",
        "HOLON_OPTIMISM_RPC_URL": "http://127.0.0.1:9",
        "HOLON_POLYGON_RPC_URL": "http://127.0.0.1:9",
        "HOLON_BSC_RPC_URL": "http://127.0.0.1:9",
        "QT_QPA_PLATFORM": "offscreen",
    })
    process = subprocess.Popen(
        [
            str(guard.resolve()), "--data-dir", str(paths.data_dir),
            "--pipe-name", pipe, "--wallet-path", str(wallet.resolve()),
            "--wallet-status-pipe-name", f"{pipe}.status",
            "--policy-control-pipe-name", f"{pipe}.policy",
        ],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=environment, creationflags=0x08000000,
    )
    try:
        wait_for_pipe(pipe, 20.0)
        client = PipeClient(pipe, 2.0, 40.0)
        opened = client.request(MessageKind.OPEN_WALLET, response_timeout=20.0)
        assert opened.kind is MessageKind.WALLET_OPENED
        preview = client.request(
            MessageKind.MODULE_ACTION_INTENT,
            {
                "module_id": "holon.perpdex",
                "capability_id": "holon.perpdex.funding.guard",
                "action_type": "FUND_TRADING_ACCOUNT",
                "params": {"amount_usdc": "6"},
            },
            response_timeout=40.0,
        )
        assert preview.kind is MessageKind.MODULE_ACTION_PREVIEW
        assert preview.payload["status"] == "PREVIEW_READY"
        assert preview.payload["preview"]["max_total_fee_wei"] == "125"
        action_id = "act-" + str(uuid.uuid4())
        started = client.request(
            MessageKind.MODULE_AUTHORITY_INTENT,
            {
                "module_id": "holon.perpdex",
                "capability_id": "holon.perpdex.funding.guard",
                "action_type": "FUND_TRADING_ACCOUNT",
                "params": {"amount_usdc": "6"},
                "preview_digest": preview.payload["preview_digest"],
            },
            action_id=action_id, owner_pid=os.getpid(), response_timeout=40.0,
        )
        assert started.kind is MessageKind.PROTECTED_FLOW_STARTED
        assert FundingRpcFixture.snapshot_index >= 3
        cancelled = client.request(MessageKind.CANCEL_ACTION, action_id=action_id)
        assert cancelled.payload["code"] == "ACTION_CANCELLED"
        assert "eth_sendRawTransaction" not in FundingRpcFixture.calls
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10,
        )
        process.wait(timeout=10)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


@pytest.mark.skipif(sys.platform != "win32", reason="Packaged Windows executables")
def test_packaged_composite_supply_starts_approve_and_cancels_without_signing(tmp_path: Path) -> None:
    guard = Path(os.environ.get("HOLON_TEST_GUARD_EXE", ""))
    wallet = Path(os.environ.get("HOLON_TEST_WALLET_EXE", ""))
    if not guard.is_file() or not wallet.is_file():
        pytest.skip("Packaged Guard and Wallet paths were not provided")
    local_root = tmp_path / "authority-local"
    paths = WalletPaths(local_root / "Holon" / "data")
    repository = VaultRepository(paths)
    record = repository.new_record(generate_mnemonic(), "Packaged Authority Fixture")
    repository.create_new("fixture-password", record)
    SettingsStore(paths).save_active_id(record.summary.profile_id)
    SnapshotStore(paths.data_dir / "guard-state.json").bootstrap_normal_for_test()
    ActionStateStore(paths.data_dir / "action-state.json").bootstrap_empty_for_test()
    RequestStateStore(
        paths.data_dir / "request-control-state.json",
    ).bootstrap_empty_for_test()
    JournalStore(paths.data_dir / "journal.jsonl").bootstrap_empty_for_test()

    RpcFixture.calls = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), RpcFixture)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    pipe = rf"\\.\pipe\Holon.Guard.packaged-authority.{uuid.uuid4()}"
    environment = dict(os.environ)
    environment.update({
        "LOCALAPPDATA": str(local_root),
        "HOLON_BASE_RPC_URL": f"http://127.0.0.1:{server.server_port}",
        "QT_QPA_PLATFORM": "offscreen",
    })
    process = subprocess.Popen(
        [str(guard.resolve()), "--pipe-name", pipe, "--wallet-path", str(wallet.resolve())],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        env=environment, creationflags=0x08000000,
    )
    action_id = f"act-{uuid.uuid4()}"
    try:
        wait_for_pipe(pipe, 20.0)
        client = PipeClient(pipe, 2.0, 40.0)
        response = client.request(
            MessageKind.LENDING_AUTHORITY_INTENT,
            {
                "module_id": "lending", "module_version": "1",
                "protocol_profile_id": "aave-v3-base-usdc",
                "protocol_profile_version": "1", "network": "base", "asset": "usdc",
                "beneficiary_mode": "active_wallet_account", "action": "supply",
                "amount_mode": "exact", "amount": "2",
            },
            action_id=action_id, owner_pid=os.getpid(), response_timeout=40.0,
        )
        assert response.kind is MessageKind.PROTECTED_FLOW_STARTED, response.payload
        assert response.payload["code"] == "AWAITING_LOCAL_CONFIRMATION"
        operation = LendingOperationStore(
            paths.data_dir / "lending-operation-state.json",
        ).load().current
        assert operation is not None
        assert operation.operation_id == action_id
        assert operation.phase == "approve_review"
        assert operation.resolved_amount_atomic == 2_000_000
        cancelled = client.request(MessageKind.CANCEL_ACTION, action_id=action_id)
        assert cancelled.kind is MessageKind.ACTION_STATUS
        assert cancelled.payload["code"] == "ACTION_CANCELLED"
        assert LendingOperationStore(
            paths.data_dir / "lending-operation-state.json",
        ).load().current is None
        assert "eth_sendRawTransaction" not in RpcFixture.calls
    finally:
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10,
        )
        process.wait(timeout=10)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)
