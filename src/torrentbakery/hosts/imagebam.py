from __future__ import annotations

import re
from pathlib import Path

import httpx

from .base import Uploaded

BASE = "https://imagebam.com"


class Imagebam:
    """imagebam's web uploader: CSRF token → /upload/session → /upload (file) → /upload/complete page with the links."""

    name = "imagebam"
    formats = ("jpg", "png", "gif")  # WebP not confirmed, so animations go up as GIF

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
                # the form field is `files[0]` (Dropzone uploadMultiple); `file`/`file[0]` give HTTP 500
                up = c.post(BASE + "/upload", headers=hdr, data={"_token": csrf, "data": s["data"]}, files={"files[0]": (path.name, f)})
            up.raise_for_status()
            done = c.get(up.json()["success"]).text
        page = re.search(r'https://(?:www\.)?imagebam\.com/view/\w+', done)
        thumb = re.search(r'https://thumbs(\d*)\.imagebam\.com/([^"\'\s<>\[]+?)_t(\.\w+)', done)
        if not (page and thumb):
            raise RuntimeError("imagebam: no image link on the result page")
        # The viewer page sits behind an ad interstitial; the direct file lives next to the thumbnail (verified live).
        direct = f"https://images{thumb.group(1)}.imagebam.com/{thumb.group(2)}{thumb.group(3)}"
        return Uploaded(direct, page.group(0), thumb.group(0))
