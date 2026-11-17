from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..models import Release


@dataclass
class Resolved:
    """What a provider hands to the pipeline: metadata, the local video file and, if it has one, an animated cover (Stash's own preview)."""

    release: Release
    path: Path | None = None
    cover: Path | None = None
