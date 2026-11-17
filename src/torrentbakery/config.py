from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

import tomli_w

CONFIG_PATH = Path(os.environ.get("TB_CONFIG", Path.home() / ".config/torrentbakery/config.toml"))


@dataclass
class Tracker:
    announce: list[str] = field(default_factory=list)
    source: str = ""
    private: bool = True


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
    )
    for name, t in data.get("tracker", {}).items():
        announce = t.get("announce", [])
        cfg.trackers[name] = Tracker(
            announce=[announce] if isinstance(announce, str) else list(announce),
            source=t.get("source", ""),
            private=t.get("private", True),
        )
    for name, b in data.get("stashbox", {}).items():
        cfg.stashboxes[name] = StashBoxCfg(b["endpoint"], b.get("api_key", ""))
    return cfg


def save(cfg: Config, path: Path | None = None) -> None:
    """Write the config back. The file holds API keys, so it is created user-only (0600)."""
    path = path or CONFIG_PATH
    data: dict = {
        "template": cfg.template,
        "sheet": {"cols": cfg.sheet_cols, "rows": cfg.sheet_rows, "thumb_width": cfg.sheet_thumb_width},
        "stash": {"url": cfg.stash.url, "api_key": cfg.stash.api_key},
        "tracker": {n: {"announce": t.announce, "source": t.source, "private": t.private} for n, t in cfg.trackers.items()},
        "stashbox": {n: {"endpoint": b.endpoint, "api_key": b.api_key} for n, b in cfg.stashboxes.items()},
        "host": cfg.hosts,
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
