from __future__ import annotations

from pathlib import Path

import httpx

from ..config import Config, map_path
from ..models import Release
from . import stashbox
from .base import Resolved
from .gql import download_raw, gql

SCENE_QUERY = """
query Scene($id: ID!) {
  findScene(id: $id) {
    title details date urls
    studio { name }
    performers { name }
    tags { name }
    files { path }
    paths { webp }
    stash_ids { endpoint stash_id }
  }
  configuration { general { stashBoxes { endpoint api_key name } } }
}
"""


def resolve(cfg: Config, scene_id: str, workdir: Path, transport: httpx.BaseTransport | None = None, want_preview: bool = False) -> Resolved:
    """Stash scene → metadata + local file (+ Stash's animated WebP preview if asked for and generated). If the scene is
    linked to a stash-box Stash knows the key of, the stash-box record replaces Stash's own metadata."""
    if not cfg.stash.url:
        raise RuntimeError("Stash is not configured: tb stash set <url> --api-key <key>")
    endpoint = cfg.stash.url.rstrip("/") + "/graphql"
    data = gql(endpoint, SCENE_QUERY, {"id": scene_id}, cfg.stash.api_key, transport)
    scene = data["findScene"]
    if not scene:
        raise LookupError(f"scene {scene_id} not found in Stash")
    if not scene["files"]:
        raise LookupError(f"scene {scene_id} has no files")

    release = Release(
        title=scene["title"] or "",
        date=scene["date"] or "",
        studio=scene["studio"]["name"] if scene["studio"] else "",
        performers=[p["name"] for p in scene["performers"]],
        tags=[t["name"] for t in scene["tags"]],
        description=scene["details"] or "",
        urls=list(scene["urls"]),
        source_ids={"stash": scene_id},
    )
    boxes = {b["endpoint"]: b["api_key"] for b in data["configuration"]["general"]["stashBoxes"]}
    for sid in scene["stash_ids"]:
        if sid["endpoint"] in boxes:
            box = stashbox.fetch(sid["endpoint"], boxes[sid["endpoint"]], sid["stash_id"], transport)
            box.source_ids["stash"] = scene_id
            release = box
            break
    # The tags are the ones the user curated in Stash; the stash-box's own tags only fill in when the scene has none.
    stash_tags = [t["name"] for t in scene["tags"]]
    if stash_tags:
        release.tags = stash_tags

    preview = None
    if want_preview and scene["paths"]["webp"]:
        dest = workdir / "preview.webp"
        try:
            download_raw(scene["paths"]["webp"], dest, cfg.stash.api_key, transport)
            head = dest.read_bytes()[:12]
            if not (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
                dest = None  # not a WebP (e.g. an error page): build our own
        except httpx.HTTPError:
            dest = None  # Stash has not generated the animated preview for this scene
        preview = dest

    path = map_path(cfg, scene["files"][0]["path"])
    if not path.is_file():
        raise FileNotFoundError(f"{path} does not exist here; add a path mapping (Stash path: {scene['files'][0]['path']})")
    return Resolved(release, path, preview)
