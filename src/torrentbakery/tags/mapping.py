"""Stash tag -> tracker tag: lowercase, dot-separated. The list of ignored housekeeping tags comes from stash-empornium (Unlicense,
https://github.com/bdbenim/stash-empornium); the user's own ignored tags live in a small TOML file next to the config."""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from importlib import resources
from pathlib import Path

import tomli_w

USER_MAP_NAME = "tags.toml"


def empify(tag: str) -> str:
    """A tracker-compatible tag: lowercase, (parenthesised notes) dropped, whitespace and . _ - become one '.', other special characters vanish, max 32 characters."""
    t = re.sub(r"\([^)]*\)", " ", tag)
    t = re.sub(r"[^\w\s._-]", "", t).lower()
    return re.sub(r"[\s._-]+", ".", t).strip(".")[:32]


def _builtin_ignored() -> set[str]:
    with resources.files("torrentbakery.tags").joinpath("default-tags.toml").open("rb") as f:
        return {t.lower() for t in tomllib.load(f).get("ignored_tags", [])}


@dataclass
class TagMap:
    """Trackers write their tags lowercase and dot-separated, so every Stash tag is simply converted that way; `ignored` tags (Stash housekeeping
    like "4K Available", plus the user's own) are left out."""

    ignored: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, user_file: Path | None = None) -> "TagMap":
        u = load_user_map(user_file)
        return cls((_builtin_ignored() | {t.lower() for t in u.get("ignored", [])}) - {t.lower() for t in u.get("unignored", [])})

    def map_tag(self, tag: str) -> list[str]:
        """The tracker tag for one Stash tag, or [] when it is ignored."""
        if tag.lower() in self.ignored:
            return []
        e = empify(tag)
        return [e] if e else []

    def map_tags(self, tags: list[str]) -> list[str]:
        """The final tag list: deduplicated, order kept."""
        return list(dict.fromkeys(e for t in tags for e in self.map_tag(t)))


def load_user_map(path: Path | None = None) -> dict:
    path = path or user_map_path()
    return tomllib.loads(path.read_text()) if path.exists() else {}


def save_user_map(data: dict, path: Path | None = None) -> None:
    path = path or user_map_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        tomli_w.dump(data, f)
    tmp.replace(path)


def user_map_path() -> Path:
    """Next to the config file, so TB_CONFIG moves both."""
    from .. import config
    return Path(os.environ.get("TB_TAGS", config.CONFIG_PATH.with_name(USER_MAP_NAME)))
