import json

import httpx
import pytest
from test_publish import baked, tracker_version  # noqa: F401  (fixture)
from torf import Torrent

from torrentbakery.config import Config, QbitCfg, Tracker
from torrentbakery.publish import prepare, publish
from torrentbakery.tags import TagMap
from torrentbakery.trackers import LoginError, PublishRequest, TrackerError, get_publisher
from torrentbakery.trackers.unit3d import UNIT3D_SITES, resolution_id

TOKEN = "SECRETTOKEN123"
SITE = UNIT3D_SITES["bitporn"]


def tracker(**kw):
    return Tracker([], "", True, **{"site": "bitporn", "base_url": "https://bp.example", "cookie": TOKEN, "category": "4", **kw})


def req(tmp_path, **kw):
    t = tmp_path / "x.torrent"
    t.write_bytes(b"d4:infod4:name1:xee")
    return PublishRequest(t, "Title", "[b]post[/b]", ["big.tits", "jane.doe"], "https://img/c.jpg", resolution=1080, mediainfo="MI", **kw)


def transport(posts, post_response=None, download=b"d4:infod4:name1:xee", gets=None):
    def h(r):
        assert r.headers["authorization"] == f"Bearer {TOKEN}" and r.headers["accept"] == "application/json"
        if r.method == "POST":
            posts.append(r)
            return post_response or httpx.Response(200, json={"success": True, "data": "https://bp.example/torrent/download/777.RSSKEY", "message": "ok"})
        if gets is not None:
            gets.append(str(r.url))
        if r.url.path == "/api/torrents/777":
            return httpx.Response(200, json={"attributes": {"download_link": "https://bp.example/torrent/download/777.RSSKEY"}})
        if r.url.path.startswith("/torrent/download/"):
            return httpx.Response(200, content=download)
        return httpx.Response(200, json={"data": []})
    return httpx.MockTransport(h)


def pub(posts=None, **kw):
    return get_publisher("bp", Config(trackers={"bp": tracker(**kw)}), transport([] if posts is None else posts))


def test_fetch_form_lists_categories():
    f = pub().fetch_form()
    assert ("4", "Amateur") in f.categories and len(f.categories) == 52 and f.announce == ""


def test_bad_token_is_a_login_error():
    t = httpx.MockTransport(lambda r: httpx.Response(401, json={"message": "Unauthenticated."}))
    with pytest.raises(LoginError, match="API token"):
        get_publisher("bp", Config(trackers={"bp": tracker()}), t).fetch_form()


def test_publish_posts_the_api_fields(tmp_path):
    posts = []
    res = pub(posts, anonymous=True).publish(req(tmp_path))
    assert res.torrent_id == "777" and res.torrent_url == "https://bp.example/torrents/777"
    assert posts[0].url.path == "/api/torrents/upload"
    body = posts[0].content
    for needle in (b'name="name"\r\n\r\nTitle', b"[b]post[/b]", b'name="mediainfo"\r\n\r\nMI', b'name="category_id"\r\n\r\n4', b'name="type_id"\r\n\r\n1',
                   b'name="resolution_id"\r\n\r\n13', b'name="keywords"\r\n\r\nbig tits, jane doe', b'name="anon"\r\n\r\n1', b'name="anonymous"\r\n\r\n1',
                   b'name="torrent"; filename="x.torrent"', b"application/x-bittorrent", b'name="tmdb"\r\n\r\n0'):
        assert needle in body, needle
    assert TOKEN.encode() not in body


def test_dry_run_posts_nothing_and_shows_no_token(tmp_path):
    posts = []
    res = pub(posts).publish(req(tmp_path), dry_run=True)
    assert posts == [] and res.dry_run and res.sent["name"] == "Title" and res.sent["torrent"] == "x.torrent" and TOKEN not in json.dumps(res.sent)


def test_rejection_shows_the_trackers_reasons(tmp_path):
    answer = httpx.Response(404, json={"success": False, "message": "Validation Error.", "data": {"name": ["The name has already been taken."]}})
    with pytest.raises(TrackerError, match="upload rejected: Validation Error. name: The name has already been taken."):
        get_publisher("bp", Config(trackers={"bp": tracker()}), transport([], answer)).publish(req(tmp_path))
    with pytest.raises(TrackerError, match="HTTP 500"):
        get_publisher("bp", Config(trackers={"bp": tracker()}), transport([], httpx.Response(500, text="oops"))).publish(req(tmp_path))


def test_unknown_category_and_missing_token(tmp_path):
    with pytest.raises(TrackerError, match="not in the known list"):
        pub(category="999").publish(req(tmp_path), dry_run=True)
    with pytest.raises(ValueError, match="API token"):
        get_publisher("bp", Config(trackers={"bp": Tracker(site="bitporn", base_url="https://bp.example")}))


def test_download_uses_the_api_link():
    gets = []
    p = get_publisher("bp", Config(trackers={"bp": tracker()}), transport([], gets=gets))
    assert p.download_torrent("777").startswith(b"d") and any("/torrent/download/777.RSSKEY" in g for g in gets)


def test_resolution_ids():
    r = SITE["resolutions"]
    assert [resolution_id(r, h) for h in (360, 480, 720, 1080, 1440, 2160)] == [12, 12, 17, 13, 20, 18]


def test_publish_without_announce_uses_the_tracker_torrent(baked):  # noqa: F811
    posts, qbit = [], []
    served, served_hash = tracker_version(baked)
    cfg = Config(trackers={"bp": tracker()}, qbit=QbitCfg("http://qb:8080", "", "", "tb", True, []))

    def q(r):
        qbit.append(r.url.path)
        return httpx.Response(200, text="")
    out = publish(cfg, baked, "bp", transport=transport(posts, download=served), qbit_transport=httpx.MockTransport(q), tagmap=TagMap.load(baked / "none.toml"))
    assert out["torrent_id"] == "777" and out["qbit"]["ok"] and out["infohash"]["tracker"] == served_hash
    sent = Torrent.read(baked / "Scene.tracker.bp.torrent")
    assert sent.trackers[0][0] == "https://bp.example/announce" and sent.private and not sent.source
    assert b"jane doe" in posts[0].content and b"eve" in posts[0].content  # performers first in the keywords
    assert "/api/v2/torrents/add" in qbit
    assert prepare(cfg, baked, "bp", tagmap=TagMap.load(baked / "none.toml"), build_torrent=False).torrent.name == "Scene.tracker.bp.torrent"
