from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class AudioTrack:
    codec: str
    channels: int
    language: str = ""


@dataclass
class MediaInfo:
    filename: str
    size: int
    duration: float
    container: str
    width: int
    height: int
    fps: float
    video_codec: str
    video_bitrate: int  # bit/s, 0 if unknown
    audio: list[AudioTrack] = field(default_factory=list)
    full_text: str = ""  # raw `mediainfo` output


@dataclass
class Release:
    """Source-agnostic metadata; providers (Stash, stash-box, later TMDB) fill it."""

    title: str
    date: str = ""
    studio: str = ""
    performers: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    description: str = ""
    urls: list[str] = field(default_factory=list)
    cover_url: str = ""
    source_ids: dict[str, str] = field(default_factory=dict)
