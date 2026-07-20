"""Stable Wallet data paths and atomic JSON persistence."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class StorageError(RuntimeError):
    """A local Wallet file could not be read or replaced safely."""


@dataclass(frozen=True, slots=True)
class WalletPaths:
    data_dir: Path

    @classmethod
    def default(cls) -> WalletPaths:
        local_app_data = os.environ.get("LOCALAPPDATA")
        if not local_app_data:
            raise StorageError("Wallet data location is unavailable")
        return cls(Path(local_app_data) / "Holon" / "data")

    @property
    def vault(self) -> Path:
        return self.data_dir / "wallet-vault.json"

    @property
    def settings(self) -> Path:
        return self.data_dir / "wallet-settings.json"


def read_json(path: Path) -> Any:
    try:
        with path.open("r", encoding="utf-8") as stream:
            return json.load(stream)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise StorageError("Wallet data is unreadable") from error


def atomic_write_json(path: Path, value: object) -> None:
    descriptor = -1
    temporary: Path | None = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent,
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            descriptor = -1
            json.dump(value, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        temporary = None
    except OSError as error:
        raise StorageError("Wallet data could not be saved") from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
