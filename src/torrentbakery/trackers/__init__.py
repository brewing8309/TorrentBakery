from __future__ import annotations

import httpx

from ..config import SITE_SOURCES, Config
from .base import LoginError, PublishRequest, PublishResult, Publisher, TrackerError, UploadForm
from .gazelle import GazellePublisher
from .unit3d import UNIT3D_SITES, Unit3dPublisher

SITES = list(SITE_SOURCES)


def get_publisher(name: str, cfg: Config, transport: httpx.BaseTransport | None = None) -> Publisher:
    t = cfg.trackers.get(name)
    if t is None:
        raise KeyError(f"unknown tracker profile {name!r}; known: {', '.join(cfg.trackers) or 'none'}")
    if t.site not in SITE_SOURCES:
        raise ValueError(f"tracker {name!r} has no publishing site set (one of: {', '.join(SITES)})")
    if t.site in UNIT3D_SITES:
        return Unit3dPublisher(name, t, transport)
    return GazellePublisher(name, t, transport)


__all__ = ["SITES", "GazellePublisher", "Unit3dPublisher", "LoginError", "PublishRequest", "PublishResult", "Publisher", "TrackerError", "UploadForm", "get_publisher"]
