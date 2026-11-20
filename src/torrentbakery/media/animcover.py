from __future__ import annotations

import subprocess
import tempfile
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


def _clip_command(video: Path, start: float, clip: float, width: int, fps: int, dest: Path) -> list[str]:
    """One short clip, already small. Decoding every clip in one ffmpeg run keeps a decoder per input open at once, which ate 2 GB on 4K sources."""
    return ["ffmpeg", "-v", "error", "-y", "-threads", "2", "-ss", f"{start:.3f}", "-t", f"{clip}", "-i", str(video), "-an",
            "-vf", f"fps={fps},scale={width}:-2:flags=lanczos,setpts=PTS-STARTPTS", "-c:v", "ffv1", str(dest)]


def _command(clips: list[Path], fmt: str, width: int, fps: int, q: int, dest: Path) -> list[str]:
    cmd = ["ffmpeg", "-v", "error", "-y"]
    for c in clips:
        cmd += ["-i", str(c)]
    parts = [f"[{i}:v]fps={fps},scale={width}:-2:flags=lanczos,setpts=PTS-STARTPTS[v{i}]" for i in range(len(clips))]
    joined = "".join(f"[v{i}]" for i in range(len(clips)))
    parts.append(f"{joined}concat=n={len(clips)}:v=1:a=0[cat]")
    if fmt == "gif":
        parts.append(f"[cat]split[a][b];[a]palettegen=max_colors={q}[p];[b][p]paletteuse=dither=bayer:bayer_scale=5[out]")
        codec = ["-loop", "0"]
    else:
        parts.append("[cat]null[out]")
        codec = ["-c:v", "libwebp_anim", "-quality", str(q), "-compression_level", "5", "-loop", "0"]
    return cmd + ["-filter_complex", ";".join(parts), "-map", "[out]", "-an", *codec, str(dest)]


def _run(cmd: list[str], what: str) -> None:
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        killed = " (killed, probably out of memory)" if res.returncode in (-9, 137) else ""
        raise RuntimeError(f"ffmpeg failed {what}{killed}: {res.stderr.strip()[-300:] or 'exit code ' + str(res.returncode)}")


def make_animated_cover(video: Path, duration: float, dest_dir: Path, fmt: str = "webp", clips: int = 0, clip: float = CLIP_SECONDS,
                        width: int = 480, max_bytes: int = 9_000_000, on_progress=None) -> Path:
    """Short animation of the whole video: `clips` pieces of `clip` seconds spread over the runtime, written to dest_dir/cover.<fmt>.
    Retries with smaller width/frame rate/quality until the file fits `max_bytes` (image hosts cap uploads at about 10 MB)."""
    if fmt not in LADDER:
        raise ValueError(f"cover format must be webp or gif, not {fmt!r}")
    starts = clip_starts(duration, clips or auto_clip_count(duration), clip)
    clip = min(clip, duration)
    dest = dest_dir / f"cover.{fmt}"
    # an explicit width caps the ladder, it never makes the picture bigger than asked for
    steps = [(min(w, width), f, q) for w, f, q in LADDER[fmt]]
    top_w, top_fps = steps[0][0], max(f for _, f, _ in steps)
    with tempfile.TemporaryDirectory(prefix="tb-cover-") as tmp:
        pieces = []
        for i, st in enumerate(starts):
            piece = Path(tmp) / f"clip{i:02d}.mkv"
            if on_progress:
                on_progress(i / (len(starts) + 1), f"Clip {i + 1} of {len(starts)}")
            _run(_clip_command(video, st, clip, top_w, top_fps, piece), f"cutting clip {i + 1} of the animated cover")
            pieces.append(piece)
        if on_progress:
            on_progress(len(starts) / (len(starts) + 1), "Stitching the clips together")
        for w, fps, q in dict.fromkeys(steps):
            _run(_command(pieces, fmt, w, fps, q, dest), "building the animated cover")
            if dest.stat().st_size <= max_bytes:
                return dest
    raise RuntimeError(f"animated cover is still {dest.stat().st_size // 1_000_000} MB at the smallest setting; use fewer clips")
