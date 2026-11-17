from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse

from .config import Config
from .hosts import get_uploader
from .pipeline import bake
from .sources import stash as stash_source
from .sources import stashbox as stashbox_source


def _from_stashbox_url(cfg: Config, url: str):
    u = urlparse(url)
    scene_id = u.path.rstrip("/").rsplit("/", 1)[-1]
    endpoint = f"{u.scheme}://{u.netloc}/graphql"
    box = next((b for b in cfg.stashboxes.values() if b.endpoint == endpoint), None)
    if box is None:
        raise ValueError(f"no key for {endpoint}; add one: tb stash box-add <name> {endpoint} --api-key <key>")
    return stashbox_source.fetch(endpoint, box.api_key, scene_id)


def bake_source(
    cfg: Config, source: str, out: Path, *, tracker: str | None = None, cover: Path | None = None, upload: str | None = None,
    nsfw: bool = True, stashbox: str | None = None, cover_source: str | None = None, cover_format: str | None = None, media_roots: list[str] | None = None, progress=None,
) -> Path:
    """Shared entry for CLI and web UI. `source` is a file path or `stash:<scene-id>`.
    With `media_roots`, plain files must lie inside one of them (the web UI must not read arbitrary paths)."""
    if tracker and tracker not in cfg.trackers:
        raise ValueError(f"unknown tracker profile {tracker!r}; known: {', '.join(cfg.trackers) or 'none'}")
    uploader = get_uploader(upload, cfg) if upload else None
    with tempfile.TemporaryDirectory() as tmp:
        release, auto_cover = None, None
        if source.startswith("stash:"):
            if progress:
                progress("Querying Stash")
            # Stash's preview is WebP: only fetch it when the chosen host takes WebP, otherwise build our own GIF
            want = (cover_source or cfg.cover_source) == "stash" and (uploader is None or "webp" in uploader.formats)
            r = stash_source.resolve(cfg, source[6:], Path(tmp), want_preview=want)
            release, path, auto_cover = r.release, r.path, r.cover
        else:
            path = Path(source).resolve()
            if media_roots is not None and not any(path.is_relative_to(Path(r).resolve()) for r in media_roots):
                raise ValueError("files outside the configured media roots are not allowed (use stash:<id> or set [web] media_roots)")
            if not path.is_file():
                raise ValueError(f"{source} is not a file (use stash:<id> for Stash scenes)")
            if stashbox:
                release = _from_stashbox_url(cfg, stashbox)
        return bake(path, out, cfg, release=release, tracker=tracker, cover=cover or auto_cover, cover_format=cover_format, uploader=uploader, nsfw=nsfw, progress=progress)
