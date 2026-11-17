from __future__ import annotations

import httpx

from ..config import Config
from .base import Uploaded, Uploader
from .hamsterimg import Hamsterimg
from .imagebam import Imagebam
from .imgbox import Imgbox
from .pixhost import DOMAINS, Pixhost

HOST_NAMES = [*DOMAINS, "hamsterimg", "imagebam", "imgbox"]


def get_uploader(name: str, cfg: Config, transport: httpx.BaseTransport | None = None) -> Uploader:
    opts = dict(cfg.hosts.get(name, {}))
    if name in DOMAINS:
        return Pixhost(domain=name, transport=transport)
    classes = {"hamsterimg": Hamsterimg, "imagebam": Imagebam, "imgbox": Imgbox}
    if name not in classes:
        raise KeyError(f"unknown image host {name!r}; known: {', '.join(HOST_NAMES)}")
    return classes[name](transport=transport, **opts)


__all__ = ["HOST_NAMES", "Uploaded", "Uploader", "get_uploader"]
