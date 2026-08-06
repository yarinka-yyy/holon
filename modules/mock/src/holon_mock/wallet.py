"""Secret-free Wallet presentation for the mock module."""

from __future__ import annotations


def create_page_model() -> dict[str, object]:
    return {
        "body": "This read-only module proves deterministic optional-module loading.",
        "moduleId": "holon.mock",
        "title": "Mock Module",
    }
