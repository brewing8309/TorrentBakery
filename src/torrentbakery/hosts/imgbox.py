from __future__ import annotations

import re
from pathlib import Path

import httpx

from .base import Uploaded

BASE = "https://imgbox.com"


class Imgbox:
    """imgbox has no public API: this replays what its upload page does (CSRF token → upload token → upload/process)."""

    name = "imgbox"
    formats = ("jpg", "png", "gif")  # WebP not confirmed, so animations go up as GIF

    def __init__(self, transport: httpx.BaseTransport | None = None, **_):
        self.transport = transport

    def upload(self, path: Path, nsfw: bool = True) -> Uploaded:
        with httpx.Client(transport=self.transport, timeout=120, headers={"User-Agent": "Mozilla/5.0 TorrentBakery"}, follow_redirects=True) as c:
            home = c.get(BASE + "/").text
            m = re.search(r'name="csrf-token"\s+content="([^"]+)"', home) or re.search(r'content="([^"]+)"\s+name="csrf-token"', home)
            if not m:
                raise RuntimeError("imgbox: no CSRF token on the upload page")
            hdr = {"X-CSRF-Token": m.group(1), "X-Requested-With": "XMLHttpRequest", "Referer": BASE + "/"}
            tok = c.post(BASE + "/ajax/token/generate", headers=hdr)
            tok.raise_for_status()
            t = tok.json()
            with open(path, "rb") as f:
                res = c.post(
                    BASE + "/upload/process", headers=hdr,
                    data={"token_id": t["token_id"], "token_secret": t["token_secret"], "content_type": 2 if nsfw else 1,
                          "thumbnail_size": "350r", "gallery_id": "", "gallery_secret": "", "comments_enabled": 0},
                    files={"files[]": (path.name, f)},
                )
        res.raise_for_status()
        files = res.json().get("files") or []
        if not files:
            raise RuntimeError(f"imgbox: upload rejected: {res.text[:200]}")
        j = files[0]
        return Uploaded(j["original_url"], j.get("url", ""), j.get("thumbnail_url", ""))
