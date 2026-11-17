from __future__ import annotations

import json
import re
import subprocess
from fractions import Fraction
from pathlib import Path

from ..models import AudioTrack, MediaInfo


def _run(cmd: list[str]) -> str:
    return subprocess.run(cmd, check=True, capture_output=True, text=True).stdout


def _fps(rate: str) -> float:
    try:
        return round(float(Fraction(rate)), 3)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> MediaInfo:
    data = json.loads(_run(["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]))
    fmt = data["format"]
    streams = data["streams"]
    video = next((s for s in streams if s["codec_type"] == "video" and s.get("disposition", {}).get("attached_pic") != 1), None)
    if video is None:
        raise ValueError(f"{path} has no video stream")
    audio = [
        AudioTrack(s.get("codec_name", ""), int(s.get("channels", 0)), s.get("tags", {}).get("language", ""))
        for s in streams
        if s["codec_type"] == "audio"
    ]
    try:
        full_text = _run(["mediainfo", str(path)])
        # mediainfo prints the full path; a forum post must not leak the host's directory layout
        full_text = re.sub(r"(?m)^(Complete name\s*:).*$", lambda m: f"{m.group(1)} {path.name}", full_text)
    except (OSError, subprocess.CalledProcessError):
        full_text = ""
    return MediaInfo(
        filename=path.name,
        size=int(fmt.get("size", path.stat().st_size)),
        duration=float(fmt.get("duration") or video.get("duration") or 0),
        container=fmt.get("format_long_name", fmt.get("format_name", "")),
        width=int(video["width"]),
        height=int(video["height"]),
        fps=_fps(video.get("avg_frame_rate", "0/1")),
        video_codec=video.get("codec_name", ""),
        video_bitrate=int(video.get("bit_rate") or 0),
        audio=audio,
        full_text=full_text,
        bitrate=int(fmt.get("bit_rate") or 0),
    )


def fmt_duration(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 3600:d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KiB", "MiB", "GiB"):
        if size < 1024:
            return f"{size:.2f} {unit}" if unit != "B" else f"{int(size)} B"
        size /= 1024
    return f"{size:.2f} TiB"


_CODECS = {"hevc": "HEVC", "h264": "H.264", "av1": "AV1", "vp9": "VP9", "mpeg4": "MPEG-4", "mpeg2video": "MPEG-2", "vc1": "VC-1",
           "aac": "AAC", "ac3": "AC-3", "eac3": "E-AC-3", "dts": "DTS", "mp3": "MP3", "opus": "Opus", "vorbis": "Vorbis", "flac": "FLAC", "truehd": "TrueHD"}
_LANGS = {"eng": "English", "deu": "German", "ger": "German", "fra": "French", "fre": "French", "ita": "Italian", "spa": "Spanish", "por": "Portuguese",
          "rus": "Russian", "jpn": "Japanese", "kor": "Korean", "zho": "Chinese", "chi": "Chinese", "nld": "Dutch", "dut": "Dutch", "pol": "Polish",
          "swe": "Swedish", "tur": "Turkish", "ces": "Czech", "cze": "Czech", "hun": "Hungarian", "ukr": "Ukrainian"}


def fmt_codec(name: str) -> str:
    return _CODECS.get(name, name.upper())


def fmt_language(code: str) -> str:
    """ISO 639-2 code to a readable name; unknown/undefined codes give an empty string."""
    return "" if code in ("", "und", "unk") else _LANGS.get(code, code)
