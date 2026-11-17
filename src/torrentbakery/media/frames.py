from __future__ import annotations

import subprocess
from pathlib import Path


def extract_frame(video: Path, t: float, dest: Path, width: int | None = None) -> Path:
    """Grab one frame at second `t` (fast input seek) as an image file."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-ss", f"{t:.3f}", "-i", str(video), "-frames:v", "1"]
    if width:
        cmd += ["-vf", f"scale={width}:-2"]
    cmd += ["-q:v", "2", str(dest)]
    subprocess.run(cmd, check=True, capture_output=True)
    return dest
