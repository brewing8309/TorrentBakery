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
    bitrate: int = 0  # overall container bit rate in bit/s (mkv streams often carry no per-stream rate)


@dataclass
class Release:
    """Source-agnostic metadata; providers (Stash, stash-box, later TMDB) fill it."""

    title: str
    date: str = ""
    studio: str = ""
    performers: list[str] = field(default_factory=list)
    male_performers: list[str] = field(default_factory=list)  # names from `performers` that are male: kept in the post, left out of title and tags
    tags: list[str] = field(default_factory=list)
    description: str = ""
    urls: list[str] = field(default_factory=list)
    cover_url: str = ""
    source_ids: dict[str, str] = field(default_factory=dict)


MALE_GENDERS = {"MALE", "TRANSGENDER_MALE"}  # Stash / stash-box gender values


def credited_performers(release: dict) -> list[str]:
    """The performers a title and the tags name: all but the male ones (a release without gender data keeps everybody)."""
    male = set(release.get("male_performers", []))
    return [p for p in release.get("performers", []) if p not in male]
