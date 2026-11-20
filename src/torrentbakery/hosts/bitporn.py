from __future__ import annotations

import re
from pathlib import Path

import httpx

from ..config import Config
from .base import Uploaded

UA = "Mozilla/5.0 (X11; Linux x86_64) TorrentBakery"


class BitpornLocal:
    """The image upload of bitporn's own upload form (POST /upload-image, max 10 MB): it needs no API key, only the tracker profile's
    web session cookie. The images end up on bitporn's image host, which bitporn shows without its proxy."""

    name = "bitporn.eu"
    site = "bitporn"  # only offered for profiles of this tracker template
    formats = ("jpg", "png", "gif", "webp")

    def __init__(self, cfg: Config, transport: httpx.BaseTransport | None = None, **_):
        t = next((t for t in cfg.trackers.values() if t.site == self.site and t.session_cookie), None)
        if t is None:
            raise ValueError("bitporn.eu as image host needs a bitporn tracker profile with a web session cookie (Settings → Trackers)")
        self.base, self.cookie, self.transport = t.effective_base_url, t.session_cookie, transport

    def upload(self, path: Path, nsfw: bool = True) -> Uploaded:
        with httpx.Client(transport=self.transport, timeout=120, follow_redirects=True, headers={"Cookie": self.cookie, "User-Agent": UA}) as c:
            page = c.get(f"{self.base}/torrents/create")
            if "login" in page.url.path:
                raise ValueError("bitporn: the web session cookie is expired; log in with "Remember me" ticked and paste both cookies (bitporn_session and remember_web_…) in the tracker profile, then it renews itself")
            m = re.search(r'<meta[^>]+name="csrf-token"[^>]+content="([^"]+)"', page.text)
            if not m:
                raise ValueError("bitporn: no CSRF token on the upload page")
            with open(path, "rb") as f:
                res = c.post(f"{self.base}/upload-image", headers={"X-CSRF-TOKEN": m.group(1), "Accept": "application/json"}, files={"image": (path.name, f)})
        res.raise_for_status()
        j = res.json()
        img = j.get("image") or {}
        if not img.get("url"):
            raise ValueError(f"bitporn upload failed: {j.get('error') or 'no image URL in the answer'}")
        return Uploaded(img["url"], img.get("url_viewer", ""), (img.get("thumb") or {}).get("url", ""))
