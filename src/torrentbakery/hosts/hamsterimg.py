from __future__ import annotations

from pathlib import Path

import httpx

from .base import Uploaded


class Hamsterimg:
    """hamsterimg.net runs Chevereto 4; its upload API needs an API key from the account dashboard."""

    name = "hamsterimg"
    default_url = "https://hamsterimg.net"
    formats = ("jpg", "png", "gif", "webp")  # Chevereto 4 default

    def __init__(self, api_key: str = "", base_url: str = "", transport: httpx.BaseTransport | None = None, **_):
        if not api_key:
            raise ValueError(f"{self.name} needs an API key: tb host set {self.name} --option api_key=...")
        self.api_key, self.base, self.transport = api_key, (base_url or self.default_url).rstrip("/"), transport

    def upload(self, path: Path, nsfw: bool = True) -> Uploaded:
        with httpx.Client(transport=self.transport, timeout=120, headers={"X-API-Key": self.api_key}) as c, open(path, "rb") as f:
            res = c.post(f"{self.base}/api/1/upload", data={"format": "json", "nsfw": 1 if nsfw else 0}, files={"source": (path.name, f)})
        res.raise_for_status()
        img = res.json()["image"]
        return Uploaded(img["url"], img.get("url_viewer", ""), (img.get("thumb") or {}).get("url", ""))


class Imghost(Hamsterimg):
    """imghost.dev is bitporn's own image host (also Chevereto). bitporn shows images of other hosts through the proxy wsrv.nl,
    which refuses adult hosts such as pixhost, so images for bitporn posts have to live here."""

    name = "imghost.dev"
    default_url = "https://imghost.dev"
