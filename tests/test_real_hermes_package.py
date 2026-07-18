from __future__ import annotations

import os
from pathlib import Path

import pytest

from package_support import build_fixture
from powershell_support import invoke


def test_official_hermes_cli_uses_only_temporary_home(tmp_path: Path) -> None:
    command_value = os.environ.get("HOLON_TEST_HERMES_COMMAND")
    if not command_value or not Path(command_value).is_file():
        pytest.skip("Hermes command was not provided")
    command = Path(command_value)
    package, _ = build_fixture(tmp_path)
    local, hermes = tmp_path / "local", tmp_path / "hermes-home"
    hermes.mkdir()
    foreign = hermes / "plugins" / "foreign"
    foreign.mkdir(parents=True)
    (foreign / "plugin.yaml").write_text(
        "name: foreign\nversion: 1.0.0\ndescription: Foreign plugin\n", encoding="utf-8",
    )
    (foreign / "__init__.py").write_text("def register(ctx):\n    pass\n", encoding="utf-8")
    (hermes / "config.yaml").write_text(
        "plugins:\n  enabled:\n    - foreign\n  entries:\n    foreign:\n"
        "      allow_tool_override: false\n",
        encoding="utf-8",
    )
    canary = hermes / "foreign-config.canary"
    canary.write_bytes(b"foreign-config-preserved")
    code, result = invoke(
        package / "install.ps1", "-PackageRoot", package,
        "-LocalAppDataRoot", local, "-HermesHome", hermes,
        "-HermesCommand", command, "-ConfirmHermesClosed", "-EnableHermesPlugin",
    )
    assert code == 0, result
    assert canary.read_bytes() == b"foreign-config-preserved"
    assert (foreign / "plugin.yaml").is_file()
    config_text = "\n".join(
        path.read_text(encoding="utf-8", errors="replace")
        for path in hermes.rglob("*") if path.is_file() and path.suffix in {".yaml", ".yml", ".json"}
    )
    assert "holon" in config_text
    assert "foreign" in config_text
    assert "allow_tool_override: true" not in config_text.lower()
    code, result = invoke(
        package / "uninstall.ps1", "-LocalAppDataRoot", local,
        "-HermesHome", hermes, "-HermesCommand", command, "-ConfirmHermesClosed",
    )
    assert code == 0, result
    assert canary.read_bytes() == b"foreign-config-preserved"
    assert (foreign / "plugin.yaml").is_file()
    assert "foreign" in (hermes / "config.yaml").read_text(encoding="utf-8")
