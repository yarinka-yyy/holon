"""Hermes entry point with self-contained shared packages."""

from importlib import import_module
import sys


def _load_shared(name: str) -> None:
    relative_name = f"{__name__}.{name}"
    try:
        module = import_module(f".{name}", __name__)
    except ModuleNotFoundError as exc:
        if exc.name != relative_name:
            raise
        import_module(name)
    else:
        sys.modules[name] = module


_load_shared("holon_contracts")
_load_shared("holon_guard_ipc")

from .plugin import register  # noqa: E402

__all__ = ["register"]
