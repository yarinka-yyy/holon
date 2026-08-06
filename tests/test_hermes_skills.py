from __future__ import annotations

import json
import os
from pathlib import Path
import re
import shutil
import subprocess

import pytest


ROOT = Path(__file__).parents[1]
SKILLS_ROOT = ROOT / "skills" / "crypto"
PLUGIN_MANIFEST = ROOT / "src" / "holon_hermes_plugin" / "plugin.yaml"
HERMES_PYTHON_ENV = "HOLON_TEST_HERMES_PYTHON"
EXPECTED_SKILLS = {"holon", "holon-earn", "holon-lending"}
INDEX_TRIGGERS = {
    "holon": "Holon, wallet, crypto, transfer, Earn",
    "holon-earn": "Holon Earn, yield, APY, return",
    "holon-lending": "Holon Lending, Aave, Compound, Morpho",
}
LANGUAGE_RULE = (
    "Always reply in the language of the user's latest meaningful message."
)


def _content(name: str) -> str:
    return (SKILLS_ROOT / name / "SKILL.md").read_text(encoding="utf-8")


def _frontmatter_and_body(name: str) -> tuple[dict[str, str], str, str]:
    content = _content(name)
    assert content.startswith("---\n")
    end = content.find("\n---\n", 4)
    assert end > 4
    frontmatter = content[4:end]
    body = content[end + 5 :]
    fields: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if line and not line[0].isspace() and ":" in line:
            key, value = line.split(":", 1)
            fields[key] = value.strip().strip('"')
    return fields, frontmatter, body


def _provided_tools() -> set[str]:
    lines = PLUGIN_MANIFEST.read_text(encoding="utf-8").splitlines()
    start = lines.index("provides_tools:") + 1
    end = lines.index("provides_hooks:")
    return {line.removeprefix("  - ") for line in lines[start:end] if line.strip()}


def test_skill_source_is_exact_and_compact() -> None:
    skill_files = set(SKILLS_ROOT.glob("*/SKILL.md"))
    assert {path.parent.name for path in skill_files} == EXPECTED_SKILLS
    assert len(skill_files) == 3
    for name in EXPECTED_SKILLS:
        fields, frontmatter, body = _frontmatter_and_body(name)
        assert fields == {
            "name": name,
            "description": fields["description"],
            "version": "0.1.0-alpha",
            "author": "Holon",
            "license": "Apache-2.0",
            "platforms": "[windows]",
            "metadata": "",
        }
        assert 1 <= len(fields["description"]) <= 500
        assert len(_content(name)) <= 8_000
        assert "  hermes:" in frontmatter
        related = {
            "holon": "holon-earn, holon-lending",
            "holon-earn": "holon, holon-lending",
            "holon-lending": "holon, holon-earn",
        }[name]
        assert f"related_skills: [{related}]" in frontmatter
        assert body.strip()


def test_language_rule_is_first_and_scenario_contracts_are_present() -> None:
    required = {
        "holon": (
            "only `/holon`",
            "holon_health",
            "holon_open_wallet",
            "Transfer workflow",
            "PROTECTED_FLOW_STARTED",
            "Never ask the user to type or paste a seed phrase",
            "holon-lending",
        ),
        "holon-earn": (
            "only `/holon-earn`",
            "holon_earn_portfolio",
            "SUPPLY_APY",
            "TRAILING_RETURN",
            "NOT_ASSESSED",
            "total explicitly incomplete",
            "load and follow `holon-lending`",
        ),
        "holon-lending": (
            "only `/holon-lending`",
            "Aave V3",
            "Compound III",
            "Morpho V1",
            "If the user did not name a protocol",
            "amount=null",
            "A separate preview is not mandatory",
            "Both exact Supply and Supply all are supported",
            "Hermes must not call `holon_lending_execute` again",
            "Resume, Revoke, or Cancel",
            "resume_or_revoke",
            "do not call `holon_recover_action` for it",
            "holon_open_wallet",
        ),
    }
    for name, snippets in required.items():
        _, _, body = _frontmatter_and_body(name)
        rules = body.index("## Non-negotiable rules")
        first_rule = body.index("1. **Always reply", rules)
        second_rule = body.index("2. **", first_rule)
        assert LANGUAGE_RULE in body[first_rule:second_rule]
        for snippet in snippets:
            assert snippet in body


def test_skill_tool_references_match_plugin_manifest_exactly() -> None:
    references: set[str] = set()
    for name in EXPECTED_SKILLS:
        references.update(re.findall(r"\bholon_[a-z0-9_]+\b", _content(name)))
    assert references == _provided_tools()


def _hermes_loader_code(agent_root: Path) -> str:
    return f"""
import json
import sys

sys.path.insert(0, {str(agent_root)!r})
from agent.prompt_builder import build_skills_system_prompt
from agent.skill_commands import get_skill_commands
from tools.skills_tool import skill_view

index = build_skills_system_prompt(
    available_tools={{"skills_list", "skill_view", "skill_manage"}}
)
commands = get_skill_commands()
views = {{
    name: json.loads(skill_view(name, preprocess=False))
    for name in ("holon", "holon-earn", "holon-lending")
}}
print(json.dumps({{
    "python": list(sys.version_info[:2]),
    "index": index,
    "commands": {{key: value["name"] for key, value in commands.items()}},
    "views": views,
}}))
"""


def test_installed_hermes_discovers_skills_in_isolated_home(tmp_path: Path) -> None:
    runtime = os.environ.get(HERMES_PYTHON_ENV)
    if not runtime or not Path(runtime).is_file():
        pytest.skip("Hermes Python 3.11 was not provided")
    agent_root = Path(runtime).resolve().parents[2]
    if not (agent_root / "agent" / "prompt_builder.py").is_file():
        pytest.skip("Hermes Agent source root could not be derived from its Python path")

    isolated_home = tmp_path / "hermes-home"
    destination = isolated_home / "skills" / "crypto"
    shutil.copytree(SKILLS_ROOT, destination)
    environment = os.environ.copy()
    environment["HERMES_HOME"] = str(isolated_home)
    environment["HERMES_PLATFORM"] = "windows"
    environment.pop("PYTHONPATH", None)
    completed = subprocess.run(
        [runtime, "-I", "-c", _hermes_loader_code(agent_root)],
        check=True,
        capture_output=True,
        text=True,
        timeout=20,
        env=environment,
        creationflags=0x08000000,
    )
    result = json.loads(completed.stdout)

    assert result["python"] == [3, 11]
    assert set(result["commands"]) == {"/holon", "/holon-earn", "/holon-lending"}
    assert set(result["commands"].values()) == EXPECTED_SKILLS
    for name in EXPECTED_SKILLS:
        fields, _, body = _frontmatter_and_body(name)
        assert f"- {name}: {fields['description'][:57]}..." in result["index"]
        assert INDEX_TRIGGERS[name] in fields["description"][:57]
        view = result["views"][name]
        assert view["success"] is True
        assert view["name"] == name
        assert view["description"] == fields["description"]
        assert body.strip() in view["content"]
        assert Path(view["skill_dir"]).is_relative_to(isolated_home)
    assert (isolated_home / ".skills_prompt_snapshot.json").is_file()
