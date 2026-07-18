from __future__ import annotations

import unittest
from unittest.mock import patch

from holon_hermes_plugin.guard import (
    GuardAvailability,
    GuardConnector,
    GuardHealth,
    GuardState,
    SubprocessGuardLauncher,
)


class FakeClient:
    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls = 0

    def probe(self) -> GuardHealth:
        result = self.results[self.calls]
        self.calls += 1
        if isinstance(result, Exception):
            raise result
        return result  # type: ignore[return-value]


class FakeLauncher:
    def __init__(self, failure: Exception | None = None) -> None:
        self.failure = failure
        self.calls = 0

    def start(self) -> None:
        self.calls += 1
        if self.failure:
            raise self.failure


class FakeProcess:
    def __init__(self, running: bool = True) -> None:
        self.running = running
        self.terminated = False

    def poll(self) -> None | int:
        return None if self.running else 3

    def terminate(self) -> None:
        self.terminated = True


class GuardConnectorTests(unittest.TestCase):
    def test_available_probe_does_not_launch(self) -> None:
        client = FakeClient(GuardHealth.available(GuardState.NORMAL))
        launcher = FakeLauncher()
        result = GuardConnector(client, launcher).ensure_available()
        self.assertEqual(result.state, GuardState.NORMAL)
        self.assertEqual(client.calls, 1)
        self.assertEqual(launcher.calls, 0)

    def test_unavailable_probe_launches_once_and_reprobes_once(self) -> None:
        client = FakeClient(
            GuardHealth.unavailable(),
            GuardHealth.available(GuardState.NORMAL),
        )
        launcher = FakeLauncher()
        result = GuardConnector(client, launcher).ensure_available()
        self.assertEqual(result.availability, GuardAvailability.AVAILABLE)
        self.assertEqual(client.calls, 2)
        self.assertEqual(launcher.calls, 1)

    def test_launch_failure_is_safely_unavailable(self) -> None:
        client = FakeClient(GuardHealth.unavailable())
        launcher = FakeLauncher(RuntimeError("private implementation detail"))
        result = GuardConnector(client, launcher).ensure_available()
        self.assertEqual(result, GuardHealth.unavailable())
        self.assertEqual(client.calls, 1)
        self.assertEqual(launcher.calls, 1)

    def test_malformed_health_is_uncertain_without_launch(self) -> None:
        client = FakeClient({"state": "NORMAL"})
        launcher = FakeLauncher()
        result = GuardConnector(client, launcher).ensure_available()
        self.assertEqual(result, GuardHealth.uncertain())
        self.assertEqual(launcher.calls, 0)

    def test_probe_exception_is_uncertain(self) -> None:
        result = GuardConnector(FakeClient(RuntimeError("detail")), FakeLauncher()).probe()
        self.assertEqual(result, GuardHealth.uncertain())

    def test_available_unknown_state_is_uncertain(self) -> None:
        client = FakeClient(GuardHealth.available(GuardState.UNKNOWN))
        result = GuardConnector(client, FakeLauncher()).probe()
        self.assertEqual(result, GuardHealth.uncertain())

    def test_guard_text_is_normalized_before_crossing_boundary(self) -> None:
        raw = GuardHealth(GuardAvailability.AVAILABLE, GuardState.NORMAL, "PRIVATE_CODE", "private")
        result = GuardConnector(FakeClient(raw), FakeLauncher()).probe()
        self.assertEqual(result, GuardHealth.available(GuardState.NORMAL))

    @patch("holon_hermes_plugin.launcher.wait_for_pipe")
    @patch("holon_hermes_plugin.launcher.subprocess.Popen")
    def test_test_launcher_uses_fixed_command_and_bounded_wait(self, popen, wait) -> None:
        popen.return_value = FakeProcess()
        SubprocessGuardLauncher(("python", "guard.py"), "test-pipe").start()
        self.assertEqual(popen.call_args.args[0], ["python", "guard.py"])
        self.assertFalse(popen.call_args.kwargs["shell"])
        wait.assert_called_once_with("test-pipe", 3.0)

    @patch("holon_hermes_plugin.launcher.wait_for_pipe", side_effect=TimeoutError())
    @patch("holon_hermes_plugin.launcher.subprocess.Popen")
    def test_test_launcher_terminates_failed_start(self, popen, _wait) -> None:
        process = FakeProcess()
        popen.return_value = process
        with self.assertRaises(TimeoutError):
            SubprocessGuardLauncher(("python",), "test-pipe").start()
        self.assertTrue(process.terminated)


if __name__ == "__main__":
    unittest.main()
