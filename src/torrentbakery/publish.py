"""Publish a baked release: build the tracker-specific torrent, post it to the tracker, hand it to qBittorrent."""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field, replace
from pathlib import Path

import httpx
from jinja2 import StrictUndefined
from jinja2.sandbox import SandboxedEnvironment
from torf import Torrent

from .clients import QbitError, QBittorrent, map_save_path
from .config import Config, Tracker
from .media.torrent import make_torrent
from .models import credited_performers
from .tags import TagMap, empify
from .trackers import UNIT3D_SITES, PublishRequest, TrackerError, get_publisher

DEFAULT_TITLE = ("{% if studio %}[{{ studio }}] {% endif %}{% if performers %}{{ performers_text }} - {% endif %}{{ title }} "
                 "{% if date %}({{ date }}) {% endif %}[{{ resolution }}]")
RESOLUTIONS = (144, 240, 360, 480, 540, 720, 1080, 1440, 2160, 4320)


@dataclass
class Prepared:
    tracker: str
    title: str
    description: str
    tags: list[str]
    category: str
    image: str
    torrent: Path
    suggestions: dict[str, str] = field(default_factory=dict)  # unknown stash tag -> proposed tracker tag
    meta: dict = field(default_factory=dict)


def _resolution(media: dict) -> str:
    h = max(media.get("height", 0), round(media.get("width", 0) * 9 / 16))
    return f"{max((r for r in RESOLUTIONS if r <= h * 1.02), default=h)}p"


def _performers_text(names: list[str]) -> str:
    return names[0] if len(names) == 1 else " & ".join(names) if len(names) == 2 else ", ".join(names[:-1]) + " & " + names[-1] if names else ""


def render_title(template: str, release: dict, media: dict) -> str:
    env = SandboxedEnvironment(undefined=StrictUndefined, autoescape=False)
    perf = credited_performers(release)  # male performers are named neither in the title nor in the tags
    return " ".join(env.from_string(template or DEFAULT_TITLE).render(
        studio=release.get("studio", ""), performers=perf, performers_text=_performers_text(perf), title=release.get("title", ""),
        date=release.get("date", ""), resolution=_resolution(media), codec=media.get("video_codec", ""),
        duration=media.get("duration", 0), framerate=media.get("fps", 0)).split())


def performer_tags(release: dict) -> list[str]:
    return [t for t in (empify(p) for p in credited_performers(release)) if t]


def _mediainfo(out_dir: Path) -> str:
    try:
        return (out_dir / "mediainfo.txt").read_text()
    except OSError:
        return ""


def _read_meta(out_dir: Path) -> dict:
    try:
        return json.loads((out_dir / "release.json").read_text())
    except (OSError, ValueError) as e:
        raise TrackerError(f"{out_dir} holds no bake (release.json missing or damaged)") from e


def _content_path(out_dir: Path, meta: dict, torrent: Path) -> Path:
    """What the torrent hashes: the file or folder next to which the release lives."""
    if meta.get("content_path") and Path(meta["content_path"]).exists():
        return Path(meta["content_path"])
    name = Torrent.read(torrent).name  # bakes made before content_path was recorded
    folder = Path(meta.get("folder_path", ""))
    for cand in (folder / name, folder.parent / name):
        if cand.exists():
            return cand
    raise TrackerError(f"cannot find the media for {name!r} (looked in {folder}); is the share mounted?")


def _matches(torrent: Path, t: Tracker) -> bool:
    try:
        tor = Torrent.read(torrent)
    except Exception:
        return False
    announces = {u for tier in tor.trackers for u in tier}
    return bool(t.announce) and announces == set(t.announce) and (tor.source or "") == t.effective_source and bool(tor.private) == t.private


def prepare(cfg: Config, out_dir: Path, name: str, *, title: str | None = None, tags: list[str] | None = None, category: str | None = None,
            tagmap: TagMap | None = None, build_torrent: bool = True, progress=None) -> Prepared:
    t = cfg.trackers.get(name)
    if t is None:
        raise KeyError(f"unknown tracker profile {name!r}")
    if not t.announce:
        if t.site not in UNIT3D_SITES:
            raise TrackerError(f"tracker {name!r} has no announce URL (tb tracker fetch-announce {name})")
        t = replace(t, announce=[t.base_url.rstrip("/") + "/announce"])  # UNIT3D rewrites the announce with your passkey on upload; any URL will do
    meta = _read_meta(out_dir)
    release, media = meta["release"], meta["media"]
    suggestions: dict[str, str] = {}
    if tags is None:
        mapped, suggestions = (tagmap or TagMap.load()).map_tags(release.get("tags", []), name)
        tags = mapped
    tags = list(dict.fromkeys(performer_tags(release) + tags))  # performers are always tagged, first, also when the tag field was edited by hand
    base = next(iter(sorted(out_dir.glob("*.torrent"), key=lambda p: ".tracker." in p.name)), None)
    if base is None:
        raise TrackerError(f"{out_dir} holds no .torrent")
    torrent = out_dir / f"{base.stem.split('.tracker.')[0]}.tracker.{name}.torrent"
    if not build_torrent:
        torrent = next((p for p in (torrent, base) if p.exists()), base)
    elif not (torrent.exists() and _matches(torrent, t)):
        if _matches(base, t):
            torrent = base
        else:
            make_torrent(_content_path(out_dir, meta, base), torrent, Tracker(t.announce, t.effective_source, t.private), progress)
    image = (meta.get("uploads", {}).get("cover") or {}).get("direct_url", "")
    return Prepared(name, title or render_title(t.title_template, release, media), (out_dir / "post.bbcode").read_text(), tags, category or t.category,
                    image, torrent, suggestions, meta)


def _seed(cfg: Config, out_dir: Path, name: str, torrent_id: str, local: Path, meta: dict, pub, qbit_transport, step) -> tuple[dict, dict]:
    """Fetch the torrent the tracker serves for `torrent_id` and add it to qBittorrent. Returns (qbit result, infohashes).
    Seeds the tracker's file, not our local one: the tracker rewrites uploads, which can change the infohash."""
    hashes: dict = {}
    try:
        step("Fetching the tracker's torrent")
        got = out_dir / f"{local.name.split('.tracker.')[0].removesuffix('.torrent')}.tracker.{name}.downloaded.torrent"  # ".tracker." keeps it from being taken for the base torrent
        got.write_bytes(pub.download_torrent(torrent_id))
        tor = Torrent.read(got)
        hashes = {"tracker": tor.infohash, "local": Torrent.read(local).infohash}
        step("Adding to qBittorrent")
        content = _content_path(out_dir, meta, local)
        QBittorrent(cfg.qbit, qbit_transport).add(got, tor.infohash, map_save_path(str(content.parent), cfg.qbit.path_map))
        note = "" if tor.infohash == hashes["local"] else " (the tracker changed the torrent, so its own file was used)"
        return {"ok": True, "message": "added the tracker's torrent" + (" and started" if cfg.qbit.start else " (paused)") + note}, hashes
    except (QbitError, TrackerError, httpx.HTTPError, OSError, ValueError) as e:
        return {"ok": False, "message": f"nothing added to qBittorrent: {e}. Download the torrent from the tracker and add it yourself."}, hashes


def seed(cfg: Config, out_dir: Path, name: str, *, transport: httpx.BaseTransport | None = None, qbit_transport: httpx.BaseTransport | None = None, progress=None) -> dict:
    """Add an already published release to qBittorrent again (e.g. after fixing the qBittorrent settings). Returns the qBit result."""
    step = progress or (lambda stage, frac=0.0: None)
    if not cfg.qbit.url:
        raise QbitError("qBittorrent is not configured")
    f = out_dir / "publish.json"
    try:
        published = json.loads(f.read_text())[name]
    except (OSError, ValueError, KeyError):
        raise TrackerError(f"this release was not published on {name!r}")
    p = prepare(cfg, out_dir, name, build_torrent=False)
    result, hashes = _seed(cfg, out_dir, name, published["torrent_id"], p.torrent, p.meta, get_publisher(name, cfg, transport), qbit_transport, step)
    all_ = json.loads(f.read_text())
    all_[name].update({"qbit": result, "infohash": hashes})
    f.write_text(json.dumps(all_, indent=2, ensure_ascii=False))
    return result


def publish(cfg: Config, out_dir: Path, name: str, *, title: str | None = None, tags: list[str] | None = None, category: str | None = None,
            dry_run: bool = False, tagmap: TagMap | None = None, transport: httpx.BaseTransport | None = None,
            qbit_transport: httpx.BaseTransport | None = None, progress=None) -> dict:
    """Returns the result dict that is also stored in <out_dir>/publish.json (unless dry_run)."""
    step = progress or (lambda stage, frac=0.0: None)
    step("Preparing upload")
    p = prepare(cfg, out_dir, name, title=title, tags=tags, category=category, tagmap=tagmap, build_torrent=not dry_run,
                progress=lambda f: step("Hashing torrent", f))
    if not p.image and not dry_run:
        raise TrackerError("this bake has no image-host upload; the tracker needs a cover URL. Bake again with an image host.")
    pub = get_publisher(name, cfg, transport)
    step("Uploading to " + name)
    res = pub.publish(PublishRequest(p.torrent, p.title, p.description, p.tags, p.image, p.category, cfg.trackers[name].anonymous, mediainfo=_mediainfo(out_dir),
                                    resolution=int(_resolution(p.meta["media"]).rstrip("p") or 0)), dry_run=dry_run)
    out = {"tracker": name, "dry_run": dry_run, "title": p.title, "tags": p.tags, "category": p.category, "torrent": p.torrent.name,
           "torrent_url": res.torrent_url, "torrent_id": res.torrent_id, "sent": res.sent, "suggestions": p.suggestions, "qbit": None, "time": time.time()}
    if dry_run:
        return out
    # the upload is public from here on: a qBittorrent problem is reported, never raised
    if cfg.qbit.url:
        out["qbit"], out["infohash"] = _seed(cfg, out_dir, name, res.torrent_id, p.torrent, p.meta, pub, qbit_transport, step)
    f = out_dir / "publish.json"
    try:
        old = json.loads(f.read_text())
    except (OSError, ValueError):
        old = {}
    old[name] = {k: v for k, v in out.items() if k != "sent"}
    f.write_text(json.dumps(old, indent=2, ensure_ascii=False))
    return out
