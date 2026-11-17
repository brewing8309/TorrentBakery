from __future__ import annotations

from pathlib import Path

import httpx

from .base import Uploaded


class Hamsterimg:
    """hamsterimg.net runs Chevereto 4; its upload API needs an API key from the account dashboard."""

    name = "hamsterimg"

    def __init__(self, api_key: str = "", base_url: str = "https://hamsterimg.net", transport: httpx.BaseTransport | None = None, **_):
        if not api_key:
            raise ValueError("hamsterimg needs an API key: tb host set hamsterimg --option api_key=...")
        self.api_key, self.base, self.transport = api_key, base_url.rstrip("/"), transport

    def upload(self, path: Path, nsfw: bool = True) -> Uploaded:
        with httpx.Client(transport=self.transport, timeout=120, headers={"X-API-Key": self.api_key}) as c, open(path, "rb") as f:
            res = c.post(f"{self.base}/api/1/upload", data={"format": "json", "nsfw": 1 if nsfw else 0}, files={"source": (path.name, f)})
        res.raise_for_status()
        img = res.json()["image"]
        return Uploaded(img["url"], img.get("url_viewer", ""), (img.get("thumb") or {}).get("url", ""))
