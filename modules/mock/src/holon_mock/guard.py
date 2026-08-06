"""Read-only mock contribution for Guard lifecycle tests."""

from __future__ import annotations

from typing import Mapping


def create_reader():
    def read(operation: str, params: Mapping[str, object]) -> dict[str, object]:
        if operation != "status" or params:
            raise ValueError("Unsupported mock read")
        return {
            "module_id": "holon.mock",
            "network_used": False,
            "status": "READY",
        }

    return read
