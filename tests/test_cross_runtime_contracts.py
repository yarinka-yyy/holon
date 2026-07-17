from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from holon_guard_ipc.client import wait_for_pipe

HERMES_PYTHON_ENV = "HOLON_TEST_HERMES_PYTHON"


@unittest.skipUnless(sys.version_info >= (3, 13), "Guard server requires Python 3.13")
class CrossRuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        hermes = os.environ.get(HERMES_PYTHON_ENV)
        if not hermes or not Path(hermes).is_file():
            self.skipTest("Hermes Python 3.11 path was not provided")
        self.hermes = hermes
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.source = Path(__file__).parents[1] / "src"
        self.pipe = rf"\\.\pipe\Holon.Guard.contracts.{uuid.uuid4()}"
        self.process: subprocess.Popen[str] | None = None
        self.addCleanup(self._stop)

    def _server_code(self) -> str:
        policy = {
            "schema_version": "1", "policy_version": "1", "authority_enabled": True,
            "transfer_rules": [{
                "network": "base", "asset": "usdc", "chain_id": 8453,
                "max_amount_atomic": "1000000", "max_total_fee_wei": "500",
            }],
        }
        return (
            "import sys\nfrom pathlib import Path\n"
            f"sys.path.insert(0,{str(self.source)!r})\n"
            "from holon_guard import GuardLifecycle,SnapshotStore\n"
            "from holon_guard.action_store import ActionStateStore\n"
            "from holon_guard.actions import ActionLedger\n"
            "from holon_guard.authority import AuthorityService\n"
            "from holon_guard.server import GuardServer\n"
            "from holon_policy import Policy,PolicyEngine\n"
            "class H:\n    pid=202\n    def poll(self): return None\n"
            "class W:\n    def open_or_activate(self,flow_id): return H()\n"
            "    def request_close(self,handle): pass\n"
            "class O:\n    def is_alive(self,pid): return True\n"
            f"r=Path({str(self.root)!r})\ns=SnapshotStore(r/'guard-state.json')\n"
            "s.bootstrap_normal_for_test()\na=ActionStateStore(r/'action-state.json')\n"
            "l=ActionLedger(a,a.bootstrap_empty_for_test())\n"
            "g=GuardLifecycle(s,s.load(),W(),O(),l)\n"
            f"p=Policy.from_dict({policy!r})\n"
            f"GuardServer({self.pipe!r},AuthorityService(g,PolicyEngine(p))).serve_forever()\n"
        )

    def _client_code(self) -> str:
        return (
            "import json,os,sys;"
            f"sys.path.insert(0,{str(self.source)!r});"
            "from holon_contracts import MessageKind,make_envelope,new_action_id;"
            "from holon_guard_ipc import PipeClient;"
            "p={'policy_version':'1','action_type':'transfer','network':'base',"
            "'asset':'usdc','amount_atomic':'1000000',"
            "'recipient':'0x1111111111111111111111111111111111111111',"
            "'max_total_fee_wei':'500'};"
            f"c=PipeClient({self.pipe!r},2.0,1.0);"
            "h=c.request(MessageKind.HEALTH_REQUEST);"
            "ok=c.exchange(make_envelope(MessageKind.PREPARE_TRANSFER,p,action_id=new_action_id()),os.getpid());"
            "p['network']='ethereum';"
            "no=c.exchange(make_envelope(MessageKind.PREPARE_TRANSFER,p,action_id=new_action_id()),os.getpid());"
            "print(json.dumps([h.to_dict(),ok.to_dict(),no.to_dict()]))"
        )

    def _stop(self) -> None:
        if self.process is not None and self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=3)

    def test_python313_guard_and_python311_contract_client(self) -> None:
        self.process = subprocess.Popen(
            [sys.executable, "-I", "-c", self._server_code()],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=0x08000000,
        )
        wait_for_pipe(self.pipe, 3.0)
        completed = subprocess.run(
            [self.hermes, "-I", "-c", self._client_code()], check=True,
            capture_output=True, text=True, timeout=8, creationflags=0x08000000,
        )
        health, allowed, refused = json.loads(completed.stdout)
        self.assertEqual(health["payload"]["guard_state"], "NORMAL")
        self.assertEqual(allowed["kind"], "protected_flow_started")
        self.assertEqual(refused["payload"]["code"], "NETWORK_NOT_ALLOWED")


if __name__ == "__main__":
    unittest.main()
