from __future__ import annotations

import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from ..models import MediaInfo
from .frames import extract_frame
from .probe import fmt_duration, fmt_size

PAD = 8
BG = (18, 18, 18)
FG = (235, 235, 235)


def timestamps(duration: float, n: int) -> list[float]:
    """n evenly spaced points inside 5–95 % of the runtime (skips intros/credits)."""
    return [duration * (0.05 + 0.90 * (i + 0.5) / n) for i in range(n)]


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def header_lines(info: MediaInfo) -> list[str]:
    audio = ", ".join(f"{a.codec} {a.channels}ch" for a in info.audio) or "none"
    return [
        info.filename,
        f"Size: {fmt_size(info.size)}   Duration: {fmt_duration(info.duration)}",
        f"Video: {info.video_codec} {info.width}x{info.height} @ {info.fps:g} fps"
        + (f" {info.video_bitrate // 1000} kb/s" if info.video_bitrate else ""),
        f"Audio: {audio}",
    ]


def make_contact_sheet(video: Path, info: MediaInfo, dest: Path, cols: int = 5, rows: int = 7, thumb_width: int = 320) -> Path:
    n = cols * rows
    ts = timestamps(info.duration, n)
    thumb_h = round(thumb_width * info.height / info.width)
    font, small = _font(18), _font(14)
    lines = header_lines(info)
    header_h = PAD * 2 + len(lines) * 24
    width = cols * thumb_width + (cols + 1) * PAD
    height = header_h + rows * thumb_h + (rows + 1) * PAD
    sheet = Image.new("RGB", (width, height), BG)
    draw = ImageDraw.Draw(sheet)
    for i, line in enumerate(lines):
        draw.text((PAD, PAD + i * 24), line, fill=FG, font=font)
    with tempfile.TemporaryDirectory() as tmp:
        for i, t in enumerate(ts):
            frame = Image.open(extract_frame(video, t, Path(tmp) / f"{i}.jpg", thumb_width)).convert("RGB")
            frame = frame.resize((thumb_width, thumb_h))
            x = PAD + (i % cols) * (thumb_width + PAD)
            y = header_h + PAD + (i // cols) * (thumb_h + PAD)
            sheet.paste(frame, (x, y))
            label = fmt_duration(t)
            tw = draw.textlength(label, font=small)
            draw.rectangle((x + thumb_width - tw - 8, y + thumb_h - 22, x + thumb_width, y + thumb_h), fill=(0, 0, 0))
            draw.text((x + thumb_width - tw - 4, y + thumb_h - 20), label, fill=FG, font=small)
    sheet.save(dest, quality=90)
    return dest
