"""Strict declarative Hermes tool descriptors for optional modules."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any, Mapping

from .model import MODULE_MANIFEST_INVALID, ModuleContractError

MAX_TOOLSET_BYTES = 64 * 1024
MAX_TOOLS = 64
TOOLSET_FIELDS = frozenset({"tools"})
TOOL_FIELDS = frozenset({
    "capability_id", "description", "name", "operation", "parameters",
})
PARAMETER_FIELDS = frozenset({
    "additionalProperties", "properties", "required", "type",
})
_TOOL_RE = re.compile(r"^holon_[a-z][a-z0-9_]{0,62}$")
_ID_RE = re.compile(r"^[a-z][a-z0-9]*(?:[.-][a-z0-9]+)*$")


@dataclass(frozen=True, slots=True)
class HermesToolDeclaration:
    name: str
    description: str
    capability_id: str
    operation: str
    parameters: Mapping[str, object]


def _fail(message: str) -> ModuleContractError:
    return ModuleContractError(MODULE_MANIFEST_INVALID, message)


def _canonical(value: object) -> bytes:
    return (
        json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        + "\n"
    ).encode("utf-8")


def _safe_json(value: Any, depth: int = 0) -> None:
    if depth > 12:
        raise _fail("Hermes metadata is too deep")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, str):
        if len(value) > 2048 or any(ord(character) < 32 for character in value):
            raise _fail("Invalid Hermes metadata text")
        return
    if isinstance(value, float):
        raise _fail("Floating-point Hermes metadata is not allowed")
    if isinstance(value, list):
        if len(value) > 256:
            raise _fail("Hermes metadata list is too large")
        for item in value:
            _safe_json(item, depth + 1)
        return
    if isinstance(value, Mapping):
        if len(value) > 256:
            raise _fail("Hermes metadata object is too large")
        for key, item in value.items():
            if not isinstance(key, str):
                raise _fail("Invalid Hermes metadata key")
            _safe_json(item, depth + 1)
        return
    raise _fail("Invalid Hermes metadata")


def load_toolset(path: Path) -> tuple[HermesToolDeclaration, ...]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise _fail("Hermes tool descriptor is unavailable") from exc
    if not raw or len(raw) > MAX_TOOLSET_BYTES or raw.startswith(b"\xef\xbb\xbf"):
        raise _fail("Invalid Hermes tool descriptor size")
    try:
        value = json.loads(raw.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise _fail("Invalid Hermes tool descriptor JSON") from exc
    _safe_json(value)
    if not isinstance(value, Mapping) or set(value) != TOOLSET_FIELDS:
        raise _fail("Invalid Hermes toolset fields")
    tools_value = value["tools"]
    if not isinstance(tools_value, list) or not tools_value or len(tools_value) > MAX_TOOLS:
        raise _fail("Invalid Hermes tool count")
    tools: list[HermesToolDeclaration] = []
    for item in tools_value:
        if not isinstance(item, Mapping) or set(item) != TOOL_FIELDS:
            raise _fail("Invalid Hermes tool fields")
        name = item["name"]
        description = item["description"]
        capability_id = item["capability_id"]
        operation = item["operation"]
        parameters = item["parameters"]
        if not isinstance(name, str) or _TOOL_RE.fullmatch(name) is None:
            raise _fail("Invalid Hermes tool name")
        if (
            not isinstance(description, str)
            or not description
            or description.strip() != description
            or len(description) > 512
        ):
            raise _fail("Invalid Hermes tool description")
        if not isinstance(capability_id, str) or _ID_RE.fullmatch(capability_id) is None:
            raise _fail("Invalid Hermes capability id")
        if not isinstance(operation, str) or _ID_RE.fullmatch(operation) is None:
            raise _fail("Invalid Hermes operation")
        if not isinstance(parameters, Mapping) or set(parameters) != PARAMETER_FIELDS:
            raise _fail("Invalid Hermes parameter schema")
        if (
            parameters["type"] != "object"
            or type(parameters["additionalProperties"]) is not bool
            or parameters["additionalProperties"] is not False
            or not isinstance(parameters["properties"], Mapping)
            or not isinstance(parameters["required"], list)
            or any(not isinstance(field, str) for field in parameters["required"])
            or not set(parameters["required"]).issubset(parameters["properties"])
        ):
            raise _fail("Invalid Hermes parameter schema")
        tools.append(HermesToolDeclaration(
            name, description, capability_id, operation, dict(parameters),
        ))
    if tuple(sorted({tool.name for tool in tools})) != tuple(tool.name for tool in tools):
        raise _fail("Hermes tool names must be unique and sorted")
    if _canonical(value) != raw:
        raise _fail("Hermes tool descriptor JSON is not canonical")
    return tuple(tools)
