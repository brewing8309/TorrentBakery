from __future__ import annotations

from urllib.parse import urlparse

import httpx

from ..models import Release
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
        tags=[t["name"] for t in scene["tags"]],
        description=scene["details"] or "",
        urls=[page_url(endpoint, scene_id)] + [u["url"] for u in scene["urls"]],
        cover_url=pick_cover(scene["images"]),
        source_ids={endpoint: scene_id},
    )
