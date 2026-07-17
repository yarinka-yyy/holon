from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

from holon_contracts import MessageKind
from holon_guard_ipc import PipeClient
from holon_guard_ipc.client import wait_for_pipe
from guard_support import transfer_request


HERMES_PYTHON_ENV = "HOLON_TEST_HERMES_PYTHON"


@unittest.skipUnless(sys.version_info >= (3, 13), "Guard server requires Python 3.13")
class GuardProcessTests(unittest.TestCase):
    def setUp(self) -> None:
        hermes_python = os.environ.get(HERMES_PYTHON_ENV)
        if not hermes_python or not Path(hermes_python).is_file():
            self.skipTest("Hermes Python 3.11 path was not provided")
        self.hermes_python = hermes_python
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.data_dir = Path(self.temporary.name) / "guard-data"
        self.pipe = rf"\\.\pipe\Holon.Guard.process.{uuid.uuid4()}"
        self.source = Path(__file__).parents[1] / "src"
        self.processes: list[subprocess.Popen[str]] = []
        self.addCleanup(self._stop_processes)

    def _command(self) -> list[str]:
        code = (
            "import sys;"
            f"sys.path.insert(0,{str(self.source)!r});"
            "from holon_guard.__main__ import main;"
            f"raise SystemExit(main(['--data-dir',{str(self.data_dir)!r},"
            f"'--pipe-name',{self.pipe!r}]))"
        )
        return [sys.executable, "-I", "-c", code]

    def _start(self) -> subprocess.Popen[str]:
        process = subprocess.Popen(
            self._command(),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            creationflags=0x08000000,
        )
        self.processes.append(process)
        return process

    def _stop_processes(self) -> None:
        for process in self.processes:
            if process.poll() is None:
                process.terminate()
        for process in self.processes:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=3)

    def _cross_runtime_health(self) -> dict[str, object]:
        code = (
            "import json,sys;"
            f"sys.path.insert(0,{str(self.source)!r});"
            "from holon_contracts import MessageKind;from holon_guard_ipc import PipeClient;"
            f"r=PipeClient({self.pipe!r},2.0,1.0).request(MessageKind.HEALTH_REQUEST);"
            "print(json.dumps(r.to_dict()))"
        )
        completed = subprocess.run(
            [self.hermes_python, "-I", "-c", code],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=0x08000000,
        )
        return json.loads(completed.stdout)

    def test_python313_server_python311_client_and_crash_lock_recovery(self) -> None:
        first = self._start()
        wait_for_pipe(self.pipe, 3.0)
        health = self._cross_runtime_health()
        self.assertEqual(health["payload"]["guard_state"], "SIGNING_DISABLED")
        refused = PipeClient(self.pipe, 1.0, 1.0).exchange(
            transfer_request(), owner_pid=os.getpid()
        )
        self.assertEqual(refused.payload["code"], "JOURNAL_STATE_INVALID")

        snapshot_before = (self.data_dir / "guard-state.json").read_bytes()
        second = self._start()
        self.assertEqual(second.wait(timeout=3), 3)
        self.assertEqual((self.data_dir / "guard-state.json").read_bytes(), snapshot_before)
        first.terminate()
        first.wait(timeout=3)

        third = self._start()
        wait_for_pipe(self.pipe, 3.0)
        recovered = PipeClient(self.pipe, 1.0, 1.0).request(MessageKind.HEALTH_REQUEST)
        self.assertEqual(recovered.payload["guard_state"], "SIGNING_DISABLED")


if __name__ == "__main__":
    unittest.main()
