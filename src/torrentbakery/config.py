from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

CONFIG_PATH = Path(os.environ.get("TB_CONFIG", Path.home() / ".config/torrentbakery/config.toml"))


# Gazelle-style sites we can publish to, with the source flag their torrents carry
SITE_SOURCES = {"empornium": "Emp", "happyfappy": "HF", "femdomcult": "FDC", "enthralled": "Ent"}


@dataclass
class Tracker:
    announce: list[str] = field(default_factory=list)
    source: str = ""
    private: bool = True
    # publishing (all optional: a profile without `site` only controls what goes into the .torrent)
    site: str = ""  # key of SITE_SOURCES
    base_url: str = ""  # e.g. https://www.empornium.sx
    cookie: str = ""  # session cookie string ("name=value; name2=value2"), a secret
    category: str = ""  # default category id of the upload form
    anonymous: bool = False
    title_template: str = ""  # Jinja, empty = built-in default
    auto_publish: bool = False

    @property
    def effective_source(self) -> str:
        return self.source or SITE_SOURCES.get(self.site, "")


@dataclass
class QbitCfg:
    url: str = ""
    username: str = ""
    password: str = ""
    category: str = ""
    start: bool = True
    # (path as TorrentBakery sees it, path as qBittorrent sees it) for the folder that holds the release
    path_map: list[tuple[str, str]] = field(default_factory=list)


@dataclass
class StashCfg:
    url: str = ""
    api_key: str = ""


@dataclass
class StashBoxCfg:
    endpoint: str
    api_key: str = ""


@dataclass
class Config:
    sheet_cols: int = 5
    sheet_rows: int = 7
    sheet_thumb_width: int = 320
    template: str = "default.bbcode.j2"
    trackers: dict[str, Tracker] = field(default_factory=dict)
    stash: StashCfg = field(default_factory=StashCfg)
    stashboxes: dict[str, StashBoxCfg] = field(default_factory=dict)
    qbit: QbitCfg = field(default_factory=QbitCfg)
    cover_source: str = "own"  # own | stash: where the animated cover comes from (stash = Stash's own preview if the scene has one)
    cover_format: str = "webp"  # webp | gif
    cover_clips: int = 0  # 0 = automatic (4-12 depending on the runtime)
    cover_width: int = 480
    default_host: str = ""  # image host preselected in the web UI
    last_used: dict[str, str] = field(default_factory=dict)  # what the bake form had at the last bake (tracker, upload, publish, cover_source, cover_format, sfw)
    web_media_roots: list[str] = field(default_factory=list)  # dirs the web UI may bake plain files from
    hosts: dict[str, dict[str, str]] = field(default_factory=dict)  # image host options, e.g. hamsterimg api_key
    # (stash path prefix, local path prefix): maps paths Stash reports to where the share is mounted here
    path_map: list[tuple[str, str]] = field(default_factory=list)


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    sheet = data.get("sheet", {})
    stash = data.get("stash", {})
    cfg = Config(
        sheet_cols=sheet.get("cols", 5),
        sheet_rows=sheet.get("rows", 7),
        sheet_thumb_width=sheet.get("thumb_width", 320),
        template=data.get("template", "default.bbcode.j2"),
        stash=StashCfg(stash.get("url", ""), stash.get("api_key", "")),
        path_map=[(m["from"], m["to"]) for m in data.get("path_map", [])],
        hosts={n: dict(o) for n, o in data.get("host", {}).items()},
        web_media_roots=list(data.get("web", {}).get("media_roots", [])),
        default_host=data.get("default_host", ""),
        last_used={k: str(v) for k, v in data.get("last_used", {}).items()},
        cover_source=data.get("cover", {}).get("source", "own"),
        cover_format=data.get("cover", {}).get("format", "webp"),
        cover_clips=data.get("cover", {}).get("clips", 0),
        cover_width=data.get("cover", {}).get("width", 480),
    )
    for name, t in data.get("tracker", {}).items():
        announce = t.get("announce", [])
        cfg.trackers[name] = Tracker(
            announce=[announce] if isinstance(announce, str) else list(announce),
            source=t.get("source", ""),
            private=t.get("private", True),
            site=t.get("site", ""),
            base_url=t.get("base_url", ""),
            cookie=t.get("cookie", ""),
            category=str(t.get("category", "")),
            anonymous=t.get("anonymous", False),
            title_template=t.get("title_template", ""),
            auto_publish=t.get("auto_publish", False),
        )
    q = data.get("qbittorrent", {})
    cfg.qbit = QbitCfg(q.get("url", ""), q.get("username", ""), q.get("password", ""), q.get("category", ""), q.get("start", True),
                       [(m["from"], m["to"]) for m in q.get("path_map", [])])
    for name, b in data.get("stashbox", {}).items():
        cfg.stashboxes[name] = StashBoxCfg(b["endpoint"], b.get("api_key", ""))
    return cfg


def _tracker_dict(t: Tracker) -> dict:
    d = {"announce": t.announce, "source": t.source, "private": t.private}
    # publishing fields only when set, so plain profiles stay as small as before
    for k in ("site", "base_url", "cookie", "category", "title_template"):
        if getattr(t, k):
            d[k] = getattr(t, k)
    for k in ("anonymous", "auto_publish"):
        if getattr(t, k):
            d[k] = True
    return d


def save(cfg: Config, path: Path | None = None) -> None:
    """Write the config back. The file holds API keys, so it is created user-only (0600)."""
    path = path or CONFIG_PATH
    data: dict = {
        "template": cfg.template,
        "default_host": cfg.default_host,
        "last_used": cfg.last_used,
        "cover": {"source": cfg.cover_source, "format": cfg.cover_format, "clips": cfg.cover_clips, "width": cfg.cover_width},
        "sheet": {"cols": cfg.sheet_cols, "rows": cfg.sheet_rows, "thumb_width": cfg.sheet_thumb_width},
        "stash": {"url": cfg.stash.url, "api_key": cfg.stash.api_key},
        "tracker": {n: _tracker_dict(t) for n, t in cfg.trackers.items()},
        "qbittorrent": {"url": cfg.qbit.url, "username": cfg.qbit.username, "password": cfg.qbit.password, "category": cfg.qbit.category,
                        "start": cfg.qbit.start, "path_map": [{"from": a, "to": b} for a, b in cfg.qbit.path_map]},
        "stashbox": {n: {"endpoint": b.endpoint, "api_key": b.api_key} for n, b in cfg.stashboxes.items()},
        "host": cfg.hosts,
        "web": {"media_roots": cfg.web_media_roots},
        "path_map": [{"from": a, "to": b} for a, b in cfg.path_map],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        tomli_w.dump(data, f)
    tmp.replace(path)


def map_path(cfg: Config, stash_path: str) -> Path:
    for src, dst in cfg.path_map:
        if stash_path == src or stash_path.startswith(src.rstrip("/") + "/"):
            return Path(dst.rstrip("/") + stash_path[len(src.rstrip("/")):])
    return Path(stash_path)
