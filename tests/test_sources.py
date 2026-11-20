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
        if req.url.path == "/scene/7/webp":
            return httpx.Response(200, content=b"RIFF\x00\x00\x00\x00WEBPVP8 ")
        if req.url.host == "img.example":
            return httpx.Response(200, content=jpeg())
        body = json.loads(req.content)
        if req.url.host == "box.example":
            assert req.headers["ApiKey"] == "boxkey"
            assert "findScene" in body["query"] and body["variables"] == {"id": "abc"}
            return httpx.Response(200, json={"data": {"findScene": SCENE}})
        assert req.headers["ApiKey"] == "stashkey"
        return httpx.Response(200, json={"data": {
            "findScene": {"title": "Stash Title", "details": "", "date": "", "urls": [], "studio": None, "performers": [], "tags": [{"name": "stash-tag"}],
                          "files": [{"path": "/stash/clip.mp4"}], "paths": {"webp": "http://stash.example/scene/7/webp"},
                          "stash_ids": [{"endpoint": BOX, "stash_id": "abc"}]},
            "configuration": {"general": {"stashBoxes": [{"endpoint": BOX, "api_key": "boxkey", "name": "box"}]}}}})
    return h


def test_stashbox_fetch_maps_fields():
    r = stashbox.fetch(BOX, "boxkey", "abc", httpx.MockTransport(handler(None)))
    assert r.title == "Box Title" and r.date == "2025-05-06" and r.studio == "Studio X"
    assert r.performers == ["Alias", "Other"] and r.male_performers == ["Other"] and r.tags == ["t1"]
    assert r.cover_url == "https://img.example/wide.jpg"  # largest landscape
    assert r.urls[0] == "https://box.example/scenes/abc" and "https://studio.example/s/1" in r.urls


def test_stash_resolve_prefers_linked_stashbox(tmp_path):
    clip = tmp_path / "share" / "clip.mp4"
    clip.parent.mkdir()
    clip.write_bytes(b"x")
    cfg = Config(stash=StashCfg("http://stash.example", "stashkey"), path_map=[("/stash", str(clip.parent))])
    r = stash.resolve(cfg, "7", tmp_path, httpx.MockTransport(handler(clip)), want_preview=True)
    assert r.release.title == "Box Title" and r.release.source_ids["stash"] == "7"
    assert r.release.tags == ["stash-tag"]  # Stash's own tags win over the stash-box's ["t1"]
    assert r.path == clip and r.cover.name == "preview.webp"  # Stash's own animated preview
    assert stash.resolve(cfg, "7", tmp_path, httpx.MockTransport(handler(clip))).cover is None  # not asked for, not fetched


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


def test_graphql_error_body_wins_over_http_status():
    import pytest
    from torrentbakery.sources.gql import gql

    t = httpx.MockTransport(lambda r: httpx.Response(400, json={"errors": [{"message": 'Cannot query field "parent" on type "Studio".'}]}))
    with pytest.raises(RuntimeError, match="Cannot query field"):
        gql("http://stash.example/graphql", "query{x}", {}, "", t)
    with pytest.raises(httpx.HTTPStatusError):  # no GraphQL body: the status error stays
        gql("http://stash.example/graphql", "query{x}", {}, "", httpx.MockTransport(lambda r: httpx.Response(401)))


def test_stash_query_uses_stash_field_names():
    # Stash calls it parent_studio; `parent` is stash-box's name and Stash rejects it (HTTP 400, seen live)
    assert "parent {" not in stash.SCENE_QUERY


def test_clean_query_strips_release_noise():
    assert stashbox.clean_query("/m/Studio.Name.Some.Title.XXX.1080p.MP4-GRP.mp4") == "Studio Name Some Title"
    assert stashbox.clean_query("Studio - Some Title [1080p].mkv") == "Studio - Some Title"
    assert stashbox.clean_query("/x/My_Scene_Title_2160p_x265.mp4") == "My Scene Title"
    assert stashbox.clean_query("/media/Folder Name/") == "Folder Name"


def test_stashbox_search_maps_rows():
    def h(req: httpx.Request) -> httpx.Response:
        body = json.loads(req.content)
        assert req.headers["ApiKey"] == "boxkey" and body["variables"]["input"]["text"] == "some title"
        return httpx.Response(200, json={"data": {"queryScenes": {"scenes": [
            {"id": "abc", "title": "Box Title", "release_date": "2025-05-06", "studio": {"name": "Studio X"},
             "performers": [{"as": "Alias", "performer": {"name": "Real"}}], "images": SCENE["images"]}]}}})
    rows = stashbox.search(BOX, "boxkey", "some title", transport=httpx.MockTransport(h))
    assert rows == [{"id": "abc", "url": "https://box.example/scenes/abc", "title": "Box Title", "date": "2025-05-06", "studio": "Studio X",
                     "image": "https://img.example/wide.jpg", "performers": ["Alias"]}]
