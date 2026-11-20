from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import urlparse

from .config import Config
from .hosts import get_uploader
from .media.files import find_main_video
from .pipeline import bake
from .publish import publish as publish_release
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


def _recipe_done(progress, release, detail: str) -> None:
    """Tell the progress page what was found: the scene's data and (for a stash-box scene) its image."""
    if not progress:
        return
    progress("Scene found", 1.0, key="recipe", detail=detail, done=True,
             release={"title": release.title, "studio": release.studio, "date": release.date, "performers": release.performers[:10], "tags": len(release.tags),
                      "tag_sample": release.tags[:8], "description": release.description[:280]},
             **({"poster": release.cover_url} if release.cover_url else {}))


def bake_source(
    cfg: Config, source: str, out: Path, *, tracker: str | None = None, cover: Path | None = None, upload: str | None = None,
    nsfw: bool = True, stashbox: str | None = None, cover_source: str | None = None, cover_format: str | None = None, media_roots: list[str] | None = None, progress=None, publish: bool | None = None,
) -> Path:
    """Shared entry for CLI and web UI. `source` is a file path, a release folder or `stash:<scene-id>`.
    A folder becomes one torrent of the whole folder; its largest video provides media info, screenshots and cover.
    With `media_roots`, paths must lie inside one of them (the web UI must not read arbitrary paths).
    `publish` uploads the result to the tracker right away; None follows the profile's auto_publish. A failed upload never fails the bake
    (the files are done): the reason lands in publish-error.txt in the output folder."""
    if tracker and tracker not in cfg.trackers:
        raise ValueError(f"unknown tracker profile {tracker!r}; known: {', '.join(cfg.trackers) or 'none'}")
    uploader = get_uploader(upload, cfg) if upload else None
    with tempfile.TemporaryDirectory() as tmp:
        release, auto_cover, folder = None, None, None
        if source.startswith("stash:"):
            if progress:
                progress("Querying Stash", 0.0, key="recipe", poster=f"/stash-thumb/{source[6:]}")
            # Stash's preview is WebP: only fetch it when the chosen host takes WebP, otherwise build our own GIF
            want = (cover_source or cfg.cover_source) == "stash" and (uploader is None or "webp" in uploader.formats)
            r = stash_source.resolve(cfg, source[6:], Path(tmp), want_preview=want)
            release, path, auto_cover = r.release, r.path, r.cover
            if media_roots and not any(path.resolve().is_relative_to(Path(m).resolve()) for m in media_roots):
                raise ValueError(f"Stash's file {path} lies outside the configured media roots (add its folder under Settings → Media folders)")
            _recipe_done(progress, release, "Found in Stash")
        else:
            path = Path(source).resolve()
            if media_roots is not None and not any(path.is_relative_to(Path(r).resolve()) for r in media_roots):
                raise ValueError("paths outside the configured media roots are not allowed (use stash:<id> or set [web] media_roots)")
            if path.is_dir():
                folder, path = path, find_main_video(path)
            elif not path.is_file():
                raise ValueError(f"{source} is not a file or folder (use stash:<id> for Stash scenes)")
            if stashbox:
                if progress:
                    progress("Querying stash-box", 0.0, key="recipe")
                release = _from_stashbox_url(cfg, stashbox)
                _recipe_done(progress, release, "Found at the stash-box")
        result = bake(path, out, cfg, release=release, tracker=tracker, cover=cover or auto_cover, cover_format=cover_format, uploader=uploader, nsfw=nsfw, progress=progress, torrent_path=folder)
    t = cfg.trackers.get(tracker) if tracker else None
    if t and t.site and (t.auto_publish if publish is None else publish):
        err = result / "publish-error.txt"
        err.unlink(missing_ok=True)
        try:
            publish_release(cfg, result, tracker, progress=(lambda stage, frac=0.0, **info: progress(stage, frac, key="publish", **info)) if progress else None)
        except Exception as e:  # reported on the result page; the bake itself succeeded
            err.write_text(f"{type(e).__name__}: {e}\n")
    return result
