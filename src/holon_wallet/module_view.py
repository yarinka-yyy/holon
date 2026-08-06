"""Secret-free mapping for the single reviewed optional-module page slot."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from holon_modules import CapabilityRegistry, ModuleLifecycleState


@dataclass(frozen=True, slots=True)
class ModuleViewModel:
    """The complete, deliberately small data surface exposed to module QML."""

    module_id: str
    title: str
    body: str

    def to_mapping(self) -> dict[str, str]:
        return {"body": self.body, "moduleId": self.module_id, "title": self.title}


def module_page_to_map(registry: CapabilityRegistry) -> dict[str, object]:
    capabilities = registry.capabilities("wallet_page")
    if len(capabilities) != 1:
        return {}
    capability = capabilities[0]
    if registry.module_status(capability.module_id).state is not ModuleLifecycleState.READY:
        return {}
    descriptor = capability.declaration.descriptor
    model = capability.contribution
    if not isinstance(model, Mapping) or set(model) != {"body", "moduleId", "title"}:
        return {}
    if model.get("moduleId") != capability.module_id:
        return {}
    for field, maximum in (("title", 80), ("body", 512)):
        value = model.get(field)
        if (
            not isinstance(value, str)
            or not value
            or len(value) > maximum
            or any(ord(character) < 32 and character not in "\n\t" for character in value)
        ):
            return {}
    view_model = ModuleViewModel(
        module_id=capability.module_id,
        title=str(model["title"]),
        body=str(model["body"]),
    )
    root = Path(capability.resource_root or "")
    qml_path = root.joinpath(*str(descriptor["qml_path"]).split("/"))
    if not qml_path.is_file():
        return {}
    icon_source = str(descriptor["icon_source"])
    icon_url = ""
    if icon_source:
        icon_path = root.joinpath(*icon_source.split("/"))
        if not icon_path.is_file():
            return {}
        icon_url = icon_path.resolve().as_uri()
    return {
        "available": True,
        "iconSource": icon_url,
        "label": str(descriptor["label"]),
        "model": view_model.to_mapping(),
        "moduleId": capability.module_id,
        "qmlUrl": qml_path.resolve().as_uri(),
        "route": str(descriptor["route"]),
    }
