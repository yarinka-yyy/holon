from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from holon_hermes_plugin.launcher import InstalledGuardLauncher, production_launcher


class FakeProcess:
    def poll(self):
        return None

    def terminate(self) -> None:
        raise AssertionError("Clean fixed launch must not terminate")


@patch("holon_hermes_plugin.launcher.wait_for_pipe")
@patch("holon_hermes_plugin.launcher.subprocess.Popen")
def test_installed_launcher_uses_only_fixed_binary_and_integrity_mode(
    popen, wait, tmp_path: Path,
) -> None:
    popen.return_value = FakeProcess()
    plugin_root = tmp_path / "custom-hermes" / "plugins" / "holon"
    InstalledGuardLauncher(tmp_path, plugin_root, "0.18.2").start()
    command = popen.call_args.args[0]
    assert command[0] == str(tmp_path / "Holon" / "app" / "HolonGuard.exe")
    assert "--require-install-integrity" in command
    assert command[command.index("--plugin-root") + 1] == str(plugin_root)
    assert command[command.index("--hermes-version") + 1] == "0.18.2"
    assert popen.call_args.kwargs["shell"] is False
    wait.assert_called_once()


@patch("holon_hermes_plugin.launcher.metadata.version", return_value="0.18.7")
@patch("holon_hermes_plugin.launcher.wait_for_pipe")
@patch("holon_hermes_plugin.launcher.subprocess.Popen")
def test_production_launcher_passes_actual_hermes_distribution_version(
    popen, _wait, version, tmp_path: Path,
) -> None:
    popen.return_value = FakeProcess()
    plugin_file = tmp_path / "other-home" / "plugins" / "holon" / "launcher.py"
    with (
        patch.dict("os.environ", {"LOCALAPPDATA": str(tmp_path)}),
        patch("holon_hermes_plugin.launcher.__file__", str(plugin_file)),
    ):
        production_launcher().start()
    command = popen.call_args.args[0]
    assert command[command.index("--hermes-version") + 1] == "0.18.7"
    assert command[command.index("--plugin-root") + 1] == str(plugin_file.parent)
    version.assert_called_once_with("hermes-agent")
