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
    assert tools == ["holon_health", "holon_open_wallet", "holon_wallet_balances"]
    assert hooks == ["on_session_start", "pre_tool_call"]
