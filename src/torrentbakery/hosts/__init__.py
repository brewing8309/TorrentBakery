from __future__ import annotations

import httpx

from ..config import Config
from .base import Uploaded, Uploader
from .hamsterimg import Hamsterimg, Imghost
from .imagebam import Imagebam
from .imgbox import Imgbox
from .pixhost import DOMAINS, Pixhost

HOST_NAMES = [*DOMAINS, "hamsterimg", "imghost.dev", "imagebam", "imgbox"]
API_KEY_HOSTS = ("hamsterimg", "imghost.dev")  # Chevereto sites: need an API key


def get_uploader(name: str, cfg: Config, transport: httpx.BaseTransport | None = None) -> Uploader:
    opts = dict(cfg.hosts.get(name, {}))
    if name in DOMAINS:
        return Pixhost(domain=name, transport=transport)
    classes = {"hamsterimg": Hamsterimg, "imghost.dev": Imghost, "imagebam": Imagebam, "imgbox": Imgbox}
    if name not in classes:
        raise KeyError(f"unknown image host {name!r}; known: {', '.join(HOST_NAMES)}")
    return classes[name](transport=transport, **opts)


__all__ = ["API_KEY_HOSTS", "HOST_NAMES", "Uploaded", "Uploader", "get_uploader"]
