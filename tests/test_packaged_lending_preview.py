from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from eth_abi import encode
from web3 import Web3

from holon_contracts import MessageKind
from holon_guard_ipc import PipeClient
from holon_guard_ipc.client import wait_for_pipe
from holon_lending import AAVE_CONTRACTS, BASE_USDC
from holon_wallet.settings import SettingsStore
from holon_wallet.storage import WalletPaths
from holon_wallet.vault import VaultRepository
from holon_wallet.wallet_crypto import generate_mnemonic


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
                "baseFeePerGas": hex(1_000_000_000),
            }
        if method == "eth_getCode":
            return "0x01"
        if method == "eth_getTransactionCount":
            return "0x7"
        if method == "eth_maxPriorityFeePerGas":
            return hex(100_000_000)
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
            return _result(["uint256"], [10_000_000])
        if data.startswith(_selector("allowance(address,address)")):
            return _result(["uint256"], [0])
        if target == BASE_USDC.lower() and data.startswith(_selector("approve(address,uint256)")):
            return _result(["bool"], [True])
        raise AssertionError(f"Unexpected eth_call: {target} {data[:8]}")


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
        [str(guard.resolve()), "--pipe-name", pipe, "--wallet-path", str(wallet.resolve())],
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
        assert response.payload["status"] == "PREVIEW_READY"
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
