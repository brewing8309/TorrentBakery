from __future__ import annotations

import re
from pathlib import Path

import httpx

from .base import Uploaded

DOMAINS = ("pixhost.to", "pixhost.cc", "pixho.st")


class Pixhost:
    """Pixhost API v2 (https://pixhost.to/api/index.html): no key; use the API host matching the link domain you want."""

    formats = ("jpg", "png", "gif", "webp")  # per the API docs: JPEG, PNG, GIF, WebP, AVIF

    def __init__(self, domain: str = "pixhost.to", transport: httpx.BaseTransport | None = None, **_):
        if domain not in DOMAINS:
            raise ValueError(f"pixhost domain must be one of {', '.join(DOMAINS)}")
        self.name, self.domain, self.transport = domain, domain, transport

    def upload(self, path: Path, nsfw: bool = True) -> Uploaded:
        with httpx.Client(transport=self.transport, timeout=120, headers={"Accept": "application/json"}) as c, open(path, "rb") as f:
            res = c.post(f"https://api.{self.domain}/images", data={"content_type": 1 if nsfw else 0, "max_th_size": 350}, files={"img": (path.name, f)})
        res.raise_for_status()
        j = res.json()
        # The response has page + thumbnail only; the direct file sits on imgN.<domain>/images/ next to tN.<domain>/thumbs/.
        direct = re.sub(r"//t(\d+)\.([^/]+)/thumbs/", r"//img\1.\2/images/", j["th_url"])
        return Uploaded(direct, j["show_url"], j["th_url"])
