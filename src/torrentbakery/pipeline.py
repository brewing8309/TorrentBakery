from __future__ import annotations

import json
import re
import shutil
from dataclasses import asdict
from pathlib import Path

from .config import Config
from .hosts import Uploader
from .media.contactsheet import make_contact_sheet
from .media.animcover import make_animated_cover
from .media.probe import probe
from .media.torrent import make_torrent
from .models import Release
from .render.post import format_tags, render_post


def slug(name: str) -> str:
    return re.sub(r"[^\w.\-]+", "_", name).strip("_") or "release"


def bake(source: Path, out_root: Path, cfg: Config, release: Release | None = None, tracker: str | None = None, cover: Path | None = None, cover_format: str | None = None, uploader: Uploader | None = None, nsfw: bool = True, progress=None, torrent_path: Path | None = None) -> Path:
    """Bake one video file into out_root/<slug>/ (+ .zip). Returns the output dir.
    `torrent_path` is the release folder when the torrent should contain more than the video (nfo, subtitles, …)."""
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    step = progress or (lambda stage, frac=0.0: None)
    step("Reading media info")
    info = probe(source)
    target = torrent_path.resolve() if torrent_path else source
    name = target.name if target.is_dir() else target.stem
    release = release or Release(title=name)
    out = out_root / slug(name)
    out.mkdir(parents=True, exist_ok=True)

    step("Screenshot sheet")
    make_contact_sheet(source, info, out / "contactsheet.jpg", cfg.sheet_cols, cfg.sheet_rows, cfg.sheet_thumb_width)
    for old in out.glob("cover.*"):  # a re-bake may change the cover format
        old.unlink()
    if cover:
        cover_file = out / f"cover{cover.suffix.lower() or '.jpg'}"
        shutil.copyfile(cover, cover_file)
    else:
        step("Animated cover")
        fmt = cover_format or cfg.cover_format
        if uploader and fmt not in uploader.formats:
            fmt = "gif"  # the host would reject WebP
        cover_file = make_animated_cover(source, info.duration, out, fmt, cfg.cover_clips, width=cfg.cover_width)
    step("Hashing torrent")
    make_torrent(target, out / f"{name}.torrent", cfg.trackers[tracker] if tracker else None, lambda f: step("Hashing torrent", f))
    (out / "mediainfo.txt").write_text(info.full_text)
    # Without an image host the post references the local files by name.
    images, links, uploads = {"cover": cover_file.name, "sheet": "contactsheet.jpg"}, {}, {}
    if uploader:
        step(f"Uploading images to {uploader.name}")
        uploads = {role: uploader.upload(out / file, nsfw) for role, file in (("cover", cover_file.name), ("sheet", "contactsheet.jpg"))}
        images = {role: u.direct_url for role, u in uploads.items()}
        links = {role: u.page_url for role, u in uploads.items() if u.page_url}
    step("Writing post")
    post = render_post(release, info, images, cfg.template, links=links)
    (out / "post.bbcode").write_text(post)
    (out / "tags.txt").write_text(format_tags(release.tags) + "\n")
    (out / "release.json").write_text(json.dumps({"release": asdict(release), "media": asdict(info), "image_host": uploader.name if uploader else "", "cover_file": cover_file.name, "uploads": {r: asdict(u) for r, u in uploads.items()}}, indent=2, ensure_ascii=False))
    shutil.make_archive(str(out_root / out.name), "zip", out)
    return out
