"""The two list images UNIT3D shows for a torrent: a 400x600 poster ("cover") and a 960x540 banner, cut from the release's promo image."""
from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse

import httpx
from PIL import Image, ImageOps

from ..config import Config
from ..sources.gql import download
from .files import find_main_video
from .frames import extract_frame

POSTER_SIZE, BANNER_SIZE = (400, 600), (960, 540)  # what UNIT3D resizes to anyway


def _api_key(cfg: Config, url: str) -> str:
    """Stash and stash-box serve their images only with the API key; other hosts must not get it."""
    host = urlparse(url).netloc
    if cfg.stash.url and urlparse(cfg.stash.url).netloc == host:
        return cfg.stash.api_key
    return next((b.api_key for b in cfg.stashboxes.values() if urlparse(b.endpoint).netloc == host), "")


def source_image(cfg: Config, meta: dict, work: Path, transport: httpx.BaseTransport | None = None) -> Path:
    """The release's promo image if it has one, else a frame from the video (a quarter in, where the opening credits are over)."""
    url = (meta.get("release") or {}).get("cover_url", "")
    if url:
        try:
            return download(url, work / "promo.jpg", _api_key(cfg, url), transport)
        except (httpx.HTTPError, OSError, ValueError):
            pass  # fall back to a frame
    content = Path(meta.get("content_path", ""))
    if not content.exists():
        raise FileNotFoundError("neither a promo image nor the video is available for the cover and banner")
    video = find_main_video(content) if content.is_dir() else content
    return extract_frame(video, float((meta.get("media") or {}).get("duration", 0)) * 0.25, work / "frame.jpg")


def make_artwork(cfg: Config, out_dir: Path, meta: dict, transport: httpx.BaseTransport | None = None) -> tuple[Path, Path]:
    """Writes poster.jpg and banner.jpg into the bake folder (center crop, like UNIT3D's own fit())."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Image.open(source_image(cfg, meta, Path(tmp), transport)).convert("RGB")
        poster, banner = out_dir / "poster.jpg", out_dir / "banner.jpg"
        ImageOps.fit(src, POSTER_SIZE, Image.LANCZOS).save(poster, quality=90)
        ImageOps.fit(src, BANNER_SIZE, Image.LANCZOS).save(banner, quality=90)
    return poster, banner
