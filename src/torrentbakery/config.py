from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("TB_CONFIG", Path.home() / ".config/torrentbakery/config.toml"))


@dataclass
class Tracker:
    announce: list[str] = field(default_factory=list)
    source: str = ""
    private: bool = True


@dataclass
class Config:
    sheet_cols: int = 5
    sheet_rows: int = 7
    sheet_thumb_width: int = 320
    template: str = "default.bbcode.j2"
    trackers: dict[str, Tracker] = field(default_factory=dict)


def load(path: Path | None = None) -> Config:
    path = path or CONFIG_PATH
    if not path.exists():
        return Config()
    data = tomllib.loads(path.read_text())
    sheet = data.get("sheet", {})
    cfg = Config(
        sheet_cols=sheet.get("cols", 5),
        sheet_rows=sheet.get("rows", 7),
        sheet_thumb_width=sheet.get("thumb_width", 320),
        template=data.get("template", "default.bbcode.j2"),
    )
    for name, t in data.get("tracker", {}).items():
        announce = t.get("announce", [])
        cfg.trackers[name] = Tracker(
            announce=[announce] if isinstance(announce, str) else list(announce),
            source=t.get("source", ""),
            private=t.get("private", True),
        )
    return cfg
