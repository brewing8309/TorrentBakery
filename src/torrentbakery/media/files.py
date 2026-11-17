from __future__ import annotations

import os
from pathlib import Path

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".ts", ".m2ts", ".webm", ".mpg", ".mpeg", ".flv", ".vob"}


def is_video(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTENSIONS


def find_main_video(folder: Path) -> Path:
    """The video that represents a release folder: the largest one (files named "sample" only if nothing else exists)."""
    videos = [Path(d) / f for d, _, files in os.walk(folder) for f in files if is_video(f)]
    if not videos:
        raise ValueError(f"no video file found in {folder}")
    real = [v for v in videos if "sample" not in v.name.lower()] or videos
    return max(real, key=lambda v: v.stat().st_size)
