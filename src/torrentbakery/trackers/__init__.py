from __future__ import annotations

from dataclasses import replace

import httpx

from ..config import Config
from .base import LoginError, PublishRequest, PublishResult, Publisher, TrackerError, UploadForm
from .gazelle import GazellePublisher
from .template import TrackerTemplate, get_template, load_templates, template_ids
from .unit3d import Unit3dPublisher


def get_publisher(name: str, cfg: Config, transport: httpx.BaseTransport | None = None) -> Publisher:
    t = cfg.trackers.get(name)
    if t is None:
        raise KeyError(f"unknown tracker profile {name!r}; known: {', '.join(cfg.trackers) or 'none'}")
    tpl = get_template(t.site)
    if tpl is None:
        raise ValueError(f"tracker {name!r} has no known tracker template (site {t.site!r}; available: {', '.join(template_ids())})")
    t = replace(t, base_url=t.effective_base_url)
    return (Unit3dPublisher if tpl.engine == "unit3d" else GazellePublisher)(name, t, transport)


__all__ = ["GazellePublisher", "Unit3dPublisher", "LoginError", "PublishRequest", "PublishResult", "Publisher", "TrackerError", "TrackerTemplate", "UploadForm",
           "get_publisher", "get_template", "load_templates", "template_ids"]
