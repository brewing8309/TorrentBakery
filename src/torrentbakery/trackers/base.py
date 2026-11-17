from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


class TrackerError(Exception):
    """The tracker refused or the page was not what we expected; the message is shown to the user."""


class LoginError(TrackerError):
    """The session cookie is missing, expired or wrong."""


@dataclass
class UploadForm:
    """What the tracker's upload page offers: the form fields with their defaults, the category list and the account's announce URL."""

    action: str = ""
    fields: dict[str, str] = field(default_factory=dict)
    categories: list[tuple[str, str]] = field(default_factory=list)  # (value, label)
    announce: str = ""


@dataclass
class PublishRequest:
    torrent: Path
    title: str
    description: str
    tags: list[str]
    image: str  # cover URL
    category: str = ""
    anonymous: bool = False
    mediainfo: str = ""  # raw MediaInfo text, for forms that have their own field


@dataclass
class PublishResult:
    torrent_url: str = ""
    torrent_id: str = ""
    dry_run: bool = False
    sent: dict[str, str] = field(default_factory=dict)  # what was (or would be) posted, text fields only, secrets removed


class Publisher(Protocol):
    name: str

    def fetch_form(self) -> UploadForm: ...

    def publish(self, req: PublishRequest, dry_run: bool = False) -> PublishResult: ...

    def download_torrent(self, torrent_id: str) -> bytes: ...
