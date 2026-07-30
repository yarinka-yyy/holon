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


@patch("holon_hermes_plugin.launcher.wait_for_pipe")
@patch("holon_hermes_plugin.launcher.subprocess.Popen")
def test_production_launcher_passes_selected_hermes_metadata_version(
    popen, _wait, tmp_path: Path,
) -> None:
    popen.return_value = FakeProcess()
    plugin_file = tmp_path / "other-home" / "plugins" / "holon" / "launcher.py"
    metadata_file = (
        tmp_path / "other-home" / "hermes-agent" / "venv" / "Lib"
        / "site-packages" / "hermes_agent-0.18.7.dist-info" / "METADATA"
    )
    metadata_file.parent.mkdir(parents=True)
    metadata_file.write_text(
        "Metadata-Version: 2.4\nName: hermes-agent\nVersion: 0.18.7\n",
        encoding="utf-8",
    )
    with (
        patch.dict("os.environ", {"LOCALAPPDATA": str(tmp_path)}),
        patch("holon_hermes_plugin.launcher.__file__", str(plugin_file)),
    ):
        production_launcher().start()
    command = popen.call_args.args[0]
    assert command[command.index("--hermes-version") + 1] == "0.18.7"
    assert command[command.index("--plugin-root") + 1] == str(plugin_file.parent)


def test_production_launcher_fails_closed_for_ambiguous_or_invalid_metadata(
    tmp_path: Path,
) -> None:
    plugin_file = tmp_path / "other-home" / "plugins" / "holon" / "launcher.py"
    site_packages = (
        tmp_path / "other-home" / "hermes-agent" / "venv" / "Lib" / "site-packages"
    )
    for name, content in (
        ("hermes_agent-0.18.2.dist-info", "Name: hermes-agent\nVersion: 0.18.2\n"),
        ("hermes_agent-0.18.7.dist-info", "Name: unexpected\nVersion: 0.18.7\n"),
    ):
        file = site_packages / name / "METADATA"
        file.parent.mkdir(parents=True)
        file.write_text(content, encoding="utf-8")
    with (
        patch.dict("os.environ", {"LOCALAPPDATA": str(tmp_path)}),
        patch("holon_hermes_plugin.launcher.__file__", str(plugin_file)),
    ):
        launcher = production_launcher()
    assert launcher._command[launcher._command.index("--hermes-version") + 1] == ""
