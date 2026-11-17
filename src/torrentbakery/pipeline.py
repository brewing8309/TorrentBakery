from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict
from pathlib import Path

from .config import Config
from .hosts import Uploader
from .media.contactsheet import make_contact_sheet
from .media.frames import extract_frame
from .media.probe import probe
from .media.torrent import make_torrent
from .models import Release
from .render.post import render_post


def slug(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name).strip("_") or "release"


def bake(source: Path, out_root: Path, cfg: Config, release: Release | None = None, tracker: str | None = None, cover: Path | None = None, uploader: Uploader | None = None, nsfw: bool = True, progress=None) -> Path:
    """Bake one video file into out_root/<slug>/ (+ .zip). Returns the output dir."""
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    step = progress or (lambda stage, frac=0.0: None)
    step("Reading media info")
    info = probe(source)
    release = release or Release(title=source.stem)
    out = out_root / slug(source.stem)
    out.mkdir(parents=True, exist_ok=True)

    step("Screenshot sheet")
    make_contact_sheet(source, info, out / "contactsheet.jpg", cfg.sheet_cols, cfg.sheet_rows, cfg.sheet_thumb_width)
    if cover:
        shutil.copyfile(cover, out / "cover.jpg")
    else:
        extract_frame(source, info.duration * 0.3, out / "cover.jpg")
    step("Hashing torrent")
    make_torrent(source, out / f"{source.stem}.torrent", cfg.trackers[tracker] if tracker else None, lambda f: step("Hashing torrent", f))
    (out / "mediainfo.txt").write_text(info.full_text)
    # Without an image host the post references the local files by name.
    images, links, uploads = {"cover": "cover.jpg", "sheet": "contactsheet.jpg"}, {}, {}
    if uploader:
        step(f"Uploading images to {uploader.name}")
        uploads = {role: uploader.upload(out / file, nsfw) for role, file in (("cover", "cover.jpg"), ("sheet", "contactsheet.jpg"))}
        images = {role: u.direct_url for role, u in uploads.items()}
        links = {role: u.page_url for role, u in uploads.items() if u.page_url}
    step("Writing post")
    post = render_post(release, info, images, cfg.template, links=links)
    (out / "post.bbcode").write_text(post)
    (out / "release.json").write_text(json.dumps({"release": asdict(release), "media": asdict(info), "image_host": uploader.name if uploader else "", "uploads": {r: asdict(u) for r, u in uploads.items()}}, indent=2, ensure_ascii=False))
    shutil.make_archive(str(out_root / out.name), "zip", out)
    return out
