"""HTTP helpers that keep secrets on their own site."""
from __future__ import annotations

from urllib.parse import urlparse

import httpx


def same_site(url: str, origin: str) -> bool:
    """True if `url` belongs to the site of `origin`: the same host or a sub-/parent domain of it (www.), never a downgrade from https."""
    u, o = urlparse(str(url)), urlparse(str(origin))
    a, b = (u.hostname or "").lower(), (o.hostname or "").lower()
    if not a or not b or (o.scheme == "https" and u.scheme != "https"):
        return False
    return a == b or a.endswith("." + b) or b.endswith("." + a)


def keep_on_site(origin: str, *headers: str) -> dict:
    """`event_hooks` for an httpx client: the named headers (API key, cookie) only go to `origin`'s site. httpx itself drops only
    Authorization on a redirect to another host, so without this a redirect (or a manually followed Location) would hand them on."""
    def hook(request: httpx.Request) -> None:
        if not same_site(str(request.url), origin):
            for h in headers:
                request.headers.pop(h, None)
    return {"request": [hook]}
