from __future__ import annotations

import re
from pathlib import Path

import httpx

from .base import Uploaded

BASE = "https://imagebam.com"


class Imagebam:
    """imagebam's web uploader: CSRF token → /upload/session → /upload (file) → /upload/complete page with the links."""

    name = "imagebam"

    def __init__(self, transport: httpx.BaseTransport | None = None, **_):
        self.transport = transport

    def upload(self, path: Path, nsfw: bool = True) -> Uploaded:
        with httpx.Client(transport=self.transport, timeout=120, headers={"User-Agent": "Mozilla/5.0 TorrentBakery"}, follow_redirects=True) as c:
            home = c.get(BASE + "/").text
            m = re.search(r'name="csrf-token"\s+content="([^"]+)"', home) or re.search(r'content="([^"]+)"\s+name="csrf-token"', home)
            if not m:
                raise RuntimeError("imagebam: no CSRF token on the upload page")
            csrf = m.group(1)
            hdr = {"X-CSRF-TOKEN": csrf, "X-Requested-With": "XMLHttpRequest"}
            sess = c.post(BASE + "/upload/session", headers=hdr, data={"thumbnail_size": "2", "content_type": "nsfw" if nsfw else "sfw", "comments_enabled": "false"})
            sess.raise_for_status()
            s = sess.json()
            if s.get("error"):
                raise RuntimeError("imagebam: session refused")
            with open(path, "rb") as f:
                up = c.post(BASE + "/upload", headers=hdr, data={"_token": csrf, "data": s["data"]}, files={"file": (path.name, f)})
            up.raise_for_status()
            done = c.get(BASE + "/upload/complete", params={"session": s["session"]}).text
        direct = re.search(r'https://images\d*\.imagebam\.com/[^"\'\s<>]+', done)
        if not direct:
            raise RuntimeError("imagebam: no image link on the result page")
        page = re.search(r'https://www\.imagebam\.com/view/\w+', done)
        thumb = re.search(r'https://thumbs\d*\.imagebam\.com/[^"\'\s<>]+', done)
        return Uploaded(direct.group(0), page.group(0) if page else "", thumb.group(0) if thumb else "")
