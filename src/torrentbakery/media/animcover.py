from __future__ import annotations

import subprocess
from pathlib import Path

from .contactsheet import timestamps

CLIP_SECONDS = 1.5
# (width, fps, quality/colors) from best to smallest; the first that fits the size budget wins
LADDER = {"webp": [(480, 15, 70), (480, 12, 55), (400, 12, 45), (320, 10, 40), (256, 8, 35)],
          "gif": [(480, 12, 128), (400, 10, 128), (320, 10, 96), (256, 8, 64), (200, 8, 48)]}


def auto_clip_count(duration: float) -> int:
    """About one clip per 2.5 minutes, 4–12 of them: a 30 min film gets 12 clips (18 s), a 5 min clip 4 (6 s)."""
    return max(4, min(12, int(duration // 150)))


def clip_starts(duration: float, clips: int, clip: float = CLIP_SECONDS) -> list[float]:
    """Evenly spaced clip starts inside 5–95 % of the runtime (skips intro/credits), each clip fully inside the video."""
    if duration <= clip * 2:
        return [0.0]
    return [min(max(0.0, c - clip / 2), duration - clip) for c in timestamps(duration, clips)]


def _command(video: Path, starts: list[float], clip: float, fmt: str, width: int, fps: int, q: int, dest: Path) -> list[str]:
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for s in starts:
        cmd += ["-ss", f"{s:.3f}", "-t", f"{clip}", "-i", str(video)]
    parts = [f"[{i}:v]fps={fps},scale={width}:-2:flags=lanczos,setpts=PTS-STARTPTS[v{i}]" for i in range(len(starts))]
    joined = "".join(f"[v{i}]" for i in range(len(starts)))
    parts.append(f"{joined}concat=n={len(starts)}:v=1:a=0[cat]")
    if fmt == "gif":
        parts.append(f"[cat]split[a][b];[a]palettegen=max_colors={q}[p];[b][p]paletteuse=dither=bayer:bayer_scale=5[out]")
        codec = ["-loop", "0"]
    else:
        parts.append("[cat]null[out]")
        codec = ["-c:v", "libwebp_anim", "-quality", str(q), "-compression_level", "5", "-loop", "0"]
    return cmd + ["-filter_complex", ";".join(parts), "-map", "[out]", "-an", *codec, str(dest)]


def make_animated_cover(video: Path, duration: float, dest_dir: Path, fmt: str = "webp", clips: int = 0, clip: float = CLIP_SECONDS,
                        width: int = 480, max_bytes: int = 9_000_000) -> Path:
    """Short animation of the whole video: `clips` pieces of `clip` seconds spread over the runtime, written to dest_dir/cover.<fmt>.
    Retries with smaller width/frame rate/quality until the file fits `max_bytes` (image hosts cap uploads at about 10 MB)."""
    if fmt not in LADDER:
        raise ValueError(f"cover format must be webp or gif, not {fmt!r}")
    starts = clip_starts(duration, clips or auto_clip_count(duration), clip)
    clip = min(clip, duration)
    dest = dest_dir / f"cover.{fmt}"
    # an explicit width caps the ladder, it never makes the picture bigger than asked for
    steps = [(min(w, width), f, q) for w, f, q in LADDER[fmt]]
    for w, fps, q in dict.fromkeys(steps):
        res = subprocess.run(_command(video, starts, clip, fmt, w, fps, q, dest), capture_output=True, text=True)
        if res.returncode != 0:
            raise RuntimeError(f"ffmpeg failed building the animated cover: {res.stderr.strip()[-300:]}")
        if dest.stat().st_size <= max_bytes:
            return dest
    raise RuntimeError(f"animated cover is still {dest.stat().st_size // 1_000_000} MB at the smallest setting; use fewer clips")
