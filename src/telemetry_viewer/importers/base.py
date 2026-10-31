from collections.abc import Callable
from pathlib import Path
from typing import Protocol

from telemetry_viewer.domain.models import Session


class Importer(Protocol):
    def load(self, path: Path, progress: Callable[[int], None] | None = None) -> Session: ...


class ImportErrorDetail(ValueError):
    """Safe, user-facing diagnostics for unsupported or unusable input."""
