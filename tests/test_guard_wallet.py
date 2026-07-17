from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from holon_guard.wallet import SubprocessWalletController


class FakeProcess:
    pid = 202

    def poll(self) -> int | None:
        return None


class GuardWalletTests(unittest.TestCase):
    def test_fixed_command_uses_shell_false_and_can_activate(self) -> None:
        process = FakeProcess()
        activated: list[int] = []
        closed: list[int] = []
        controller = SubprocessWalletController(
            ("mock-wallet.exe", "--fixture"),
            close_callback=lambda handle: closed.append(handle.pid),
            activate_callback=lambda handle: activated.append(handle.pid),
        )
        with patch("holon_guard.wallet.subprocess.Popen", return_value=process) as popen:
            self.assertIs(controller.open_or_activate("flow-one"), process)
            self.assertIs(controller.open_or_activate("flow-two"), process)
        command = popen.call_args.args[0]
        self.assertEqual(command, ["mock-wallet.exe", "--fixture", "flow-one"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        self.assertEqual(activated, [202])
        controller.request_close(process)
        self.assertEqual(closed, [202])

    def test_mock_fixture_process_reports_normal_and_nonzero_exit(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "mock_wallet.py"
            fixture.write_text("raise SystemExit(0)\n", encoding="utf-8")
            normal = SubprocessWalletController((sys.executable, str(fixture)))
            normal_handle = normal.open_or_activate("flow-normal")
            normal_code = normal_handle.wait(timeout=5)  # type: ignore[attr-defined]
            fixture.write_text("raise SystemExit(7)\n", encoding="utf-8")
            failed = SubprocessWalletController((sys.executable, str(fixture)))
            failed_handle = failed.open_or_activate("flow-failed")
            failed_code = failed_handle.wait(timeout=5)  # type: ignore[attr-defined]
        self.assertEqual(normal_code, 0)
        self.assertEqual(failed_code, 7)


if __name__ == "__main__":
    unittest.main()
