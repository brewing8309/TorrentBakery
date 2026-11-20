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
from .media.probe import fmt_codec, fmt_duration, fmt_size, probe
from .media.torrent import make_torrent
from .models import Release
from .render.post import release_tags, render_post


LIVE_DIR = ".live"


def slug(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "_", name).strip("_")
    return s if s.strip(".") else "release"  # "." and ".." would put the output into the output root or above it


def bake(source: Path, out_root: Path, cfg: Config, release: Release | None = None, tracker: str | None = None, cover: Path | None = None, cover_format: str | None = None, uploader: Uploader | None = None, nsfw: bool = True, progress=None, torrent_path: Path | None = None) -> Path:
    """Bake one video file into out_root/<slug>/ (+ .zip). Returns the output dir.
    `torrent_path` is the release folder when the torrent should contain more than the video (nfo, subtitles, …)."""
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    step = progress or (lambda stage, frac=0.0, **info: None)
    step("Reading media info", 0.0, key="probe")
    info = probe(source)
    target = torrent_path.resolve() if torrent_path else source
    name = target.name if target.is_dir() else target.stem
    release = release or Release(title=name)
    out = out_root / slug(name)
    if out.resolve().parent != out_root.resolve():
        raise ValueError(f"refusing to write outside {out_root}: {out}")
    out.mkdir(parents=True, exist_ok=True)
    step("Reading media info", 1.0, key="probe", detail=f"{info.width}×{info.height} · {fmt_codec(info.video_codec)} · {fmt_duration(info.duration)}",
         facts={"resolution": f"{info.width}×{info.height}", "codec": fmt_codec(info.video_codec), "duration": fmt_duration(info.duration), "size": fmt_size(info.size),
                "fps": f"{info.fps:g} fps", "aspect": round(info.width / info.height, 4) if info.height else 1.7778, "file": info.filename})
    live = out / LIVE_DIR  # frames of the sheet while it is being built, for the progress page; gone again when the bake ends
    shutil.rmtree(live, ignore_errors=True)
    live.mkdir()
    try:
        def on_frame(done: int, total: int, frame: Path) -> None:
            shutil.copyfile(frame, live / f"frame-{done - 1:02d}.jpg")
            step("Screenshot sheet", done / total, key="sheet", detail=f"Frame {done} of {total}", frames={"done": done, "total": total, "cols": cfg.sheet_cols, "rows": cfg.sheet_rows})

        step("Screenshot sheet", 0.0, key="sheet", out=str(out), frames={"done": 0, "total": cfg.sheet_cols * cfg.sheet_rows, "cols": cfg.sheet_cols, "rows": cfg.sheet_rows})
        make_contact_sheet(source, info, out / "contactsheet.jpg", cfg.sheet_cols, cfg.sheet_rows, cfg.sheet_thumb_width, on_frame)
        step("Screenshot sheet", 1.0, key="sheet", file="contactsheet.jpg", done=True)
        for old in out.glob("cover.*"):  # a re-bake may change the cover format
            old.unlink()
        if cover:
            cover_file = out / f"cover{cover.suffix.lower() or '.jpg'}"
            shutil.copyfile(cover, cover_file)
            step("Cover from Stash", 1.0, key="cover", detail="Taken from Stash's own preview", file=cover_file.name, done=True)
        else:
            fmt = cover_format or cfg.cover_format
            if uploader and fmt not in uploader.formats:
                fmt = "gif"  # the host would reject WebP
            step("Animated cover", 0.0, key="cover", detail=f"{fmt.upper()}")
            cover_file = make_animated_cover(source, info.duration, out, fmt, cfg.cover_clips, width=cfg.cover_width,
                                             on_progress=lambda f, d: step("Animated cover", f, key="cover", detail=d))
            step("Animated cover", 1.0, key="cover", file=cover_file.name, detail=f"{fmt.upper()} · {fmt_size(cover_file.stat().st_size)}", done=True)
    finally:
        shutil.rmtree(live, ignore_errors=True)
    step("Hashing torrent", 0.0, key="torrent")
    make_torrent(target, out / f"{name}.torrent", cfg.trackers[tracker] if tracker else None, lambda f: step("Hashing torrent", f, key="torrent", detail=f"{f * 100:.0f} %"))
    (out / "mediainfo.txt").write_text(info.full_text)
    # Without an image host the post references the local files by name.
    images, links, uploads = {"cover": cover_file.name, "sheet": "contactsheet.jpg"}, {}, {}
    if uploader:
        for n, (role, file) in enumerate((("cover", cover_file.name), ("sheet", "contactsheet.jpg"))):
            step(f"Uploading images to {uploader.name}", n / 2, key="upload", detail=f"{'Cover' if role == 'cover' else 'Screenshot sheet'} → {uploader.name}")
            uploads[role] = uploader.upload(out / file, nsfw)
        images = {role: u.direct_url for role, u in uploads.items()}
        links = {role: u.page_url for role, u in uploads.items() if u.page_url}
    step("Writing post", 0.0, key="post")
    post = render_post(release, info, images, cfg.template, links=links)
    (out / "post.bbcode").write_text(post)
    (out / "tags.txt").write_text(release_tags(release) + "\n")
    (out / "release.json").write_text(json.dumps({"release": asdict(release), "media": asdict(info), "image_host": uploader.name if uploader else "",
                                        # the folder a tracker upload is named after: the selected folder, or the file's parent folder
                                        "folder_name": (torrent_path.name if torrent_path else source.parent.name), "folder_path": str(torrent_path.resolve() if torrent_path else source.parent), "content_path": str(target), "cover_file": cover_file.name, "uploads": {r: asdict(u) for r, u in uploads.items()}}, indent=2, ensure_ascii=False))
    shutil.make_archive(str(out_root / out.name), "zip", out)
    return out
