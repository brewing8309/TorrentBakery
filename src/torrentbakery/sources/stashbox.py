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


_HIT = """id title release_date
      studio { name }
      performers { as performer { name } }
      images { url width height }"""
# stash-box itself answers queryScenes; ThePornDB's endpoint returns "scenes: null" there but has searchScene
SEARCH_QUERY = "query Search($input: SceneQueryInput!) { queryScenes(input: $input) { scenes { %s } } }" % _HIT
SEARCH_TERM_QUERY = "query Search($term: String!, $limit: Int) { searchScene(term: $term, limit: $limit) { %s } }" % _HIT

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


_DATE = re.compile(r"\b(\d{4}-\d{2}-\d{2})\b")


def search_terms(name: str) -> tuple[list[str], str]:
    """File name → search terms, best first, plus the release date found in it. Names like "Studio - 2024-08-23 - Performer - Title" find
    nothing as a whole in a stash-box's text search, which matches the title: so the title part is tried first, then everything but the date."""
    q = clean_query(name)
    m = _DATE.search(q)
    date = m.group(1) if m else ""
    parts = [p.strip() for p in re.split(r"\s+-\s+", q) if p.strip() and not _DATE.fullmatch(p.strip())]
    terms = ([parts[-1]] if len(parts) > 1 else []) + [" ".join(parts) if parts else q]
    if len(terms[0].split()) > 8:  # very long titles: the first words are the safest thing to match
        terms.append(" ".join(terms[0].split()[:8]))
    return list(dict.fromkeys(t for t in terms if t)), date


def find(endpoint: str, api_key: str, terms: list[str], date: str = "", limit: int = 8, transport: httpx.BaseTransport | None = None) -> list[dict]:
    """Try the terms in turn until there are enough scenes; scenes released on the file's date come first."""
    rows: dict[str, dict] = {}
    for term in terms:
        for r in search(endpoint, api_key, term, limit, transport):
            rows.setdefault(r["id"], r)
        if len(rows) >= limit:
            break
    return sorted(rows.values(), key=lambda r: bool(date) and r["date"] != date)[:limit]


def search(endpoint: str, api_key: str, term: str, limit: int = 8, transport: httpx.BaseTransport | None = None) -> list[dict]:
    """Scenes of one stash-box matching a free-text term, as short rows for the user to pick from."""
    scenes = None
    try:
        data = gql(endpoint, SEARCH_QUERY, {"input": {"text": term, "per_page": limit, "page": 1, "sort": "DATE", "direction": "DESC"}}, api_key, transport)
        scenes = data["queryScenes"]["scenes"]
    except RuntimeError as exc:
        first_error = exc
    if scenes is None:
        try:
            scenes = gql(endpoint, SEARCH_TERM_QUERY, {"term": term, "limit": limit}, api_key, transport)["searchScene"]
        except RuntimeError:
            if "first_error" in locals():
                raise first_error from None
            raise
    rows = []
    for s in scenes or []:
        rows.append({
            "id": s["id"], "url": page_url(endpoint, s["id"]), "title": s["title"] or "", "date": s["release_date"] or "",
            "studio": s["studio"]["name"] if s["studio"] else "", "image": pick_cover(s["images"] or []),
            "performers": [a["as"] or a["performer"]["name"] for a in s["performers"] or []][:4],
        })
    return rows
