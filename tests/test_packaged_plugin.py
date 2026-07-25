from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from package_support import build_fixture


def _loader_code(plugin: Path) -> str:
    return (
        "import importlib.util,json,sys,types;"
        "sys.modules['holon_contracts']=types.ModuleType('host_incompatible_contracts');"
        "p=types.ModuleType('hermes_plugins');p.__path__=[];sys.modules['hermes_plugins']=p;"
        f"r={str(plugin)!r};"
        "s=importlib.util.spec_from_file_location('hermes_plugins.holon',r+'/__init__.py',"
        "submodule_search_locations=[r]);m=importlib.util.module_from_spec(s);"
        "sys.modules['hermes_plugins.holon']=m;s.loader.exec_module(m);"
        "c=type('C',(),{'tools':[],'hooks':[],'register_tool':lambda x,**k:x.tools.append(k),"
        "'register_hook':lambda x,n,f:x.hooks.append(n)})();m.register(c);"
        "print(json.dumps([[x['name'] for x in c.tools],c.hooks]))"
    )


def _hook_code(plugin: Path) -> str:
    return f"""
import importlib.util
import json
import sys
import types

sys.modules['holon_contracts'] = types.ModuleType('host_incompatible_contracts')
namespace = types.ModuleType('hermes_plugins')
namespace.__path__ = []
sys.modules['hermes_plugins'] = namespace
root = {str(plugin)!r}
spec = importlib.util.spec_from_file_location(
    'hermes_plugins.holon', root + '/__init__.py',
    submodule_search_locations=[root],
)
module = importlib.util.module_from_spec(spec)
sys.modules['hermes_plugins.holon'] = module
spec.loader.exec_module(module)
from hermes_plugins.holon import plugin as holon_plugin
from holon_guard_ipc import GuardHealth, GuardState

class Connector:
    health = GuardHealth.available(GuardState.ACTIVE)
    def probe(self):
        return self.health

connector = Connector()
runtime = holon_plugin.PluginRuntime(connector)
allowed = [
    runtime.pre_tool_call(name) is None
    for name in (
        'holon_health', 'holon_transfer_status', 'holon_cancel_transfer',
        'holon_recover_transfer',
    )
]
blocked = [
    runtime.pre_tool_call(name)['action']
    for name in (
        'terminal', 'browser', 'future_unknown_tool', 'holon_lending_prepare',
        'holon_prepare_transfer',
    )
]
connector.health = GuardHealth.available(GuardState.NORMAL)
restored = runtime.pre_tool_call('future_unknown_tool') is None
print(json.dumps([allowed, blocked, restored]))
"""


@pytest.mark.parametrize("runtime", [sys.executable, os.environ.get("HOLON_TEST_HERMES_PYTHON")])
def test_vendored_plugin_registers_without_project_imports(tmp_path: Path, runtime: str | None) -> None:
    if not runtime or not Path(runtime).is_file():
        pytest.skip("Hermes Python 3.11 was not provided")
    package, _ = build_fixture(tmp_path)
    plugin = package / "payload" / "plugin"
    completed = subprocess.run(
        [runtime, "-I", "-c", _loader_code(plugin)], check=True,
        capture_output=True, text=True, timeout=10,
    )
    tools, hooks = json.loads(completed.stdout)
    assert tools == [
        "holon_health", "holon_open_wallet", "holon_wallet_balances",
        "holon_lending_compare", "holon_lending_positions",
        "holon_lending_prepare",
        "holon_prepare_transfer", "holon_transfer_status", "holon_cancel_transfer",
        "holon_recover_transfer",
    ]
    assert hooks == ["on_session_start", "pre_tool_call"]


@pytest.mark.parametrize("runtime", [sys.executable, os.environ.get("HOLON_TEST_HERMES_PYTHON")])
def test_vendored_plugin_hook_default_blocks_and_restores(
    tmp_path: Path, runtime: str | None,
) -> None:
    if not runtime or not Path(runtime).is_file():
        pytest.skip("Hermes Python 3.11 was not provided")
    package, _ = build_fixture(tmp_path)
    plugin = package / "payload" / "plugin"
    completed = subprocess.run(
        [runtime, "-I", "-c", _hook_code(plugin)], check=True,
        capture_output=True, text=True, timeout=10,
    )
    allowed, blocked, restored = json.loads(completed.stdout)
    assert allowed == [True, True, True, True]
    assert blocked == ["block", "block", "block", "block", "block"]
    assert restored is True
