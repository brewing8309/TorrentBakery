"""Stash tag -> tracker tag mapping. The default list and `empify` come from stash-empornium (Unlicense,
https://github.com/bdbenim/stash-empornium); user edits live in a small TOML file next to the config."""
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
    """A tracker-compatible tag: lowercase, whitespace and . _ - become one '.', other special characters vanish, max 32 characters."""
    t = re.sub(r"[^\w\s._-]", "", tag).lower()
    return re.sub(r"[\s._-]+", ".", t).strip(".")[:32]


def _default() -> dict:
    with resources.files("torrentbakery.tags").joinpath("default-tags.toml").open("rb") as f:
        e = tomllib.load(f).get("empornium", {})
    return {"tags": e.get("tags", {}), "ignored": e.get("ignored_tags", [])}


@dataclass
class TagMap:
    """`default` maps a lowercase stash tag to a list of tracker tags; `per_tracker[tracker]` overrides single entries."""

    default: dict[str, list[str]] = field(default_factory=dict)
    per_tracker: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    ignored: set[str] = field(default_factory=set)

    @classmethod
    def load(cls, user_file: Path | None = None) -> "TagMap":
        d = _default()
        m = cls({k.lower(): v.split() for k, v in d["tags"].items()}, {}, {t.lower() for t in d["ignored"]})
        u = load_user_map(user_file)
        for k, v in u.get("tags", {}).items():
            m.default[k.lower()] = v.split()
        for tr, entries in u.get("tracker", {}).items():
            m.per_tracker[tr] = {k.lower(): v.split() for k, v in entries.items()}
        m.ignored |= {t.lower() for t in u.get("ignored", [])}
        m.ignored -= {t.lower() for t in u.get("unignored", [])}
        return m

    def map_tag(self, tag: str, tracker: str = "") -> tuple[list[str], bool]:
        """(tracker tags, known). Ignored tags give ([], True); unknown ones give the empified tag and False so the UI can offer a suggestion."""
        key = tag.lower()
        if key in self.ignored:
            return [], True
        mapped = self.per_tracker.get(tracker, {}).get(key) or self.default.get(key)
        if mapped:
            return list(mapped), True
        e = empify(tag)
        return ([e] if e else []), False

    def map_tags(self, tags: list[str], tracker: str = "") -> tuple[list[str], dict[str, str]]:
        """Final tag list (deduplicated, order kept) plus the suggestions {stash tag: proposed tracker tag} for unknown tags."""
        out, suggestions = [], {}
        for t in tags:
            mapped, known = self.map_tag(t, tracker)
            if not known and mapped:
                suggestions[t] = mapped[0]
            out += mapped
        return list(dict.fromkeys(out)), suggestions


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
