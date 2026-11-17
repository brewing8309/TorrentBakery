from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass
class Uploaded:
    direct_url: str  # full-size image, for [img]
    page_url: str = ""  # host's viewer page, for [url]
    thumb_url: str = ""


class Uploader(Protocol):
    name: str
    formats: tuple[str, ...]  # image types the host accepts

    def upload(self, path: Path, nsfw: bool = True) -> Uploaded: ...
