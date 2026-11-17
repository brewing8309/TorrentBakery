import io
import json

import httpx
from PIL import Image

from torrentbakery import config as config_mod
from torrentbakery.config import Config, StashCfg, Tracker, map_path
from torrentbakery.sources import stash, stashbox

BOX = "https://box.example/graphql"
SCENE = {
    "title": "Box Title", "details": "Box details", "release_date": "2025-05-06", "code": None, "director": None,
    "urls": [{"url": "https://studio.example/s/1", "site": {"name": "Studio"}}],
    "studio": {"name": "Studio X", "parent": None},
    "tags": [{"name": "t1"}],
    "images": [{"url": "https://img.example/portrait.jpg", "width": 100, "height": 200},
               {"url": "https://img.example/wide.jpg", "width": 1920, "height": 1080},
               {"url": "https://img.example/small.jpg", "width": 640, "height": 360}],
    "performers": [{"as": "Alias", "performer": {"name": "Real", "gender": "FEMALE"}}, {"as": None, "performer": {"name": "Other", "gender": "MALE"}}],
}


def jpeg() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "JPEG")
    return buf.getvalue()


def handler(video_path):
    def h(req: httpx.Request) -> httpx.Response:
        if req.url.host == "img.example":
            return httpx.Response(200, content=jpeg())
        body = json.loads(req.content)
        if req.url.host == "box.example":
            assert req.headers["ApiKey"] == "boxkey"
            assert "findScene" in body["query"] and body["variables"] == {"id": "abc"}
            return httpx.Response(200, json={"data": {"findScene": SCENE}})
        assert req.headers["ApiKey"] == "stashkey"
        return httpx.Response(200, json={"data": {
            "findScene": {"title": "Stash Title", "details": "", "date": "", "urls": [], "studio": None, "performers": [], "tags": [],
                          "files": [{"path": "/stash/clip.mp4"}], "paths": {"screenshot": "http://stash.example/shot.jpg"},
                          "stash_ids": [{"endpoint": BOX, "stash_id": "abc"}]},
            "configuration": {"general": {"stashBoxes": [{"endpoint": BOX, "api_key": "boxkey", "name": "box"}]}}}})
    return h


def test_stashbox_fetch_maps_fields():
    r = stashbox.fetch(BOX, "boxkey", "abc", httpx.MockTransport(handler(None)))
    assert r.title == "Box Title" and r.date == "2025-05-06" and r.studio == "Studio X"
    assert r.performers == ["Alias", "Other"] and r.tags == ["t1"]
    assert r.cover_url == "https://img.example/wide.jpg"  # largest landscape
    assert r.urls[0] == "https://box.example/scenes/abc" and "https://studio.example/s/1" in r.urls


def test_stash_resolve_prefers_linked_stashbox(tmp_path):
    clip = tmp_path / "share" / "clip.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"x")
    cfg = Config(stash=StashCfg("http://stash.example", "stashkey"), path_map=[("/stash", str(clip.parent))])
    r = stash.resolve(cfg, "7", tmp_path, httpx.MockTransport(handler(clip)))
    assert r.release.title == "Box Title" and r.release.source_ids["stash"] == "7"
    assert r.path == clip and r.cover.exists()


def test_map_path():
    cfg = Config(path_map=[("/data/media", "/mnt/nas")])
    assert str(map_path(cfg, "/data/media/a/b.mp4")) == "/mnt/nas/a/b.mp4"
    assert str(map_path(cfg, "/data/mediax/b.mp4")) == "/data/mediax/b.mp4"


def test_config_roundtrip(tmp_path):
    p = tmp_path / "c.toml"
    cfg = Config(trackers={"t": Tracker(["https://a/announce"], "S", False)}, stash=StashCfg("http://s", "k"), path_map=[("/a", "/b")])
    config_mod.save(cfg, p)
    assert oct(p.stat().st_mode)[-3:] == "600"
    back = config_mod.load(p)
    assert back.trackers["t"] == cfg.trackers["t"] and back.stash.api_key == "k" and back.path_map == [("/a", "/b")]
