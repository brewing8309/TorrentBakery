from __future__ import annotations

import re
from pathlib import PurePath
from urllib.parse import urlparse

import httpx

from ..models import MALE_GENDERS, Release
from .gql import gql

SCENE_QUERY = """
query Scene($id: ID!) {
  findScene(id: $id) {
    title details release_date code director
    urls { url site { name } }
    studio { name parent { name } }
    tags { name }
    images { url width height }
    performers { as performer { name gender } }
  }
}
"""


def pick_cover(images: list[dict]) -> str:
    """Largest landscape image (a forum header wants width > height); else the largest of any shape."""
    landscape = [i for i in images if i["width"] > i["height"]]
    pool = landscape or images
    return max(pool, key=lambda i: i["width"] * i["height"])["url"] if pool else ""


def page_url(endpoint: str, scene_id: str) -> str:
    u = urlparse(endpoint)
    return f"{u.scheme}://{u.netloc}/scenes/{scene_id}"


def fetch(endpoint: str, api_key: str, scene_id: str, transport: httpx.BaseTransport | None = None) -> Release:
    scene = gql(endpoint, SCENE_QUERY, {"id": scene_id}, api_key, transport)["findScene"]
    if not scene:
        raise LookupError(f"scene {scene_id} not found at {endpoint}")
    studio = scene["studio"]["name"] if scene["studio"] else ""
    return Release(
        title=scene["title"] or "",
        date=scene["release_date"] or "",
        studio=studio,
        performers=[a["as"] or a["performer"]["name"] for a in scene["performers"]],
        male_performers=[a["as"] or a["performer"]["name"] for a in scene["performers"] if a["performer"].get("gender") in MALE_GENDERS],
        tags=[t["name"] for t in scene["tags"]],
        description=scene["details"] or "",
        urls=[page_url(endpoint, scene_id)] + [u["url"] for u in scene["urls"]],
        cover_url=pick_cover(scene["images"]),
        source_ids={endpoint: scene_id},
    )


SEARCH_QUERY = """
query Search($input: SceneQueryInput!) {
  queryScenes(input: $input) {
    scenes {
      id title release_date
      studio { name }
      performers { as performer { name } }
      images { url width height }
    }
  }
}
"""

# what release names carry around the actual title: resolution/codec/source tags, the dash-separated release group
_NOISE = re.compile(r"\b(?:\d{3,4}p|\d{3,4}x\d{3,4}|[24]k|uhd|hd|sd|fhd|hevc|[xh]\.?26[45]|av1|mp4|aac|web-?dl|web-?rip|webrip|bluray|bdrip|hdtv|xxx|mp\d|\d+fps|\d+mbps|kbps)\b", re.I)


def clean_query(name: str) -> str:
    """Release/file name → search words: no extension, no resolution/codec tags, separators as spaces."""
    stem = PurePath(name.rstrip("/")).name
    stem = re.sub(r"\.(?:mp4|mkv|avi|wmv|mov|m4v|ts|webm|flv|mpg|mpeg)$", "", stem, flags=re.I)
    stem = re.sub(r"\[[^\]]*\]|\([^)]*\)", " ", stem)  # [group] and (year/source) brackets
    stem = re.sub(r"[._]+", " ", stem)
    stem = re.sub(rf"({_NOISE.pattern})-[A-Za-z0-9]+$", r"\1", stem, flags=re.I)  # '1080p-GROUP': the group goes
    stem = _NOISE.sub(" ", stem)
    return re.sub(r"\s+", " ", stem).strip(" -")


def search(endpoint: str, api_key: str, term: str, limit: int = 8, transport: httpx.BaseTransport | None = None) -> list[dict]:
    """Scenes of one stash-box matching a free-text term, as short rows for the user to pick from."""
    data = gql(endpoint, SEARCH_QUERY, {"input": {"text": term, "per_page": limit, "page": 1, "sort": "DATE", "direction": "DESC"}}, api_key, transport)
    rows = []
    for s in data["queryScenes"]["scenes"]:
        image = pick_cover(s["images"])
        rows.append({
            "id": s["id"], "url": page_url(endpoint, s["id"]), "title": s["title"] or "", "date": s["release_date"] or "",
            "studio": s["studio"]["name"] if s["studio"] else "", "image": image,
            "performers": [a["as"] or a["performer"]["name"] for a in s["performers"]][:4],
        })
    return rows
