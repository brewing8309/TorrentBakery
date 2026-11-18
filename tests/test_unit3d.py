import json

import httpx
import pytest
from qbit_fake import FakeQbit
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
    posts = []
    served, served_hash = tracker_version(baked)
    cfg = Config(trackers={"bp": tracker()}, qbit=QbitCfg("http://qb:8080", "", "", "tb", True, []))

    fake = FakeQbit()
    out = publish(cfg, baked, "bp", transport=transport(posts, download=served), qbit_transport=fake.transport(), tagmap=TagMap.load(baked / "none.toml"))
    assert out["torrent_id"] == "777" and out["qbit"]["ok"] and out["infohash"]["tracker"] == served_hash
    sent = Torrent.read(baked / "Scene.tracker.bp.torrent")
    assert sent.trackers[0][0] == "https://bp.example/announce" and sent.private and not sent.source
    assert b"jane doe" in posts[0].content and b"eve" in posts[0].content  # performers first in the keywords
    assert "torrents/add" in fake.paths and fake.t["running"]
    assert prepare(cfg, baked, "bp", tagmap=TagMap.load(baked / "none.toml"), build_torrent=False).torrent.name == "Scene.tracker.bp.torrent"


# ---- cover and banner through the web form (bitporn: hidden cover_url / banner_url, description in a Livewire snapshot) ----
SNAPSHOT = '{&quot;data&quot;:{&quot;label&quot;:&quot;Description&quot;,&quot;name&quot;:&quot;description&quot;,&quot;contentBbcode&quot;:&quot;[b]post[/b]\\n\\nline &amp; two&quot;}}'
EDIT_PAGE = f"""<html><body><form method="POST" action="https://bp.example/torrents/777">
<input type="hidden" name="_token" value="CSRF1"><input type="hidden" name="_method" value="PATCH">
<input type="hidden" name="cover_url" id="hidden-cover-url" value=""><input type="hidden" name="banner_url" id="hidden-banner-url" value="">
<input type="text" name="name" value="Title &amp; more"><select name="category_id"><option value="4" selected>Amateur</option><option value="5">Anal</option></select>
<div wire:snapshot="{SNAPSHOT}"></div><textarea id="bbcode-description" name="description" required></textarea>
<textarea name="bdinfo"></textarea>
<input type="hidden" name="anon" value="0"><input type="checkbox" name="anon" value="1" checked>
<input type="hidden" name="internal" value="0"><input type="checkbox" name="internal" value="1">
</form></body></html>"""
SESSION = "bitporn_session=WEBSESSION"


def web_tracker(**kw):
    return tracker(session_cookie=SESSION, **kw)


def web_transport(posts, edit=EDIT_PAGE, post_response=None):
    def h(r):
        if r.url.path.startswith("/api/"):
            return httpx.Response(200, json={"data": []})
        assert r.headers["cookie"] == SESSION
        if r.method == "POST":
            posts.append(r)
            return post_response or httpx.Response(302, headers={"location": "https://bp.example/torrents/777"})
        if r.url.path == "/torrents/777/edit":
            return httpx.Response(200, text=edit)
        if r.url.path == "/login":
            return httpx.Response(200, text="login")
        return httpx.Response(200, text="home")
    return httpx.MockTransport(h)


def test_set_artwork_patches_the_form_with_both_urls_and_the_post(tmp_path):
    posts = []
    p = get_publisher("bp", Config(trackers={"bp": web_tracker()}), web_transport(posts))
    p.set_artwork("777", "https://imghost.dev/c.jpg", "https://imghost.dev/b.jpg")
    body = posts[0].content.decode()
    form = dict(__import__("urllib.parse", fromlist=["parse_qsl"]).parse_qsl(body))
    assert form["_token"] == "CSRF1" and form["_method"] == "PATCH" and form["name"] == "Title & more" and form["category_id"] == "4"
    assert form["cover_url"] == "https://imghost.dev/c.jpg" and form["banner_url"] == "https://imghost.dev/b.jpg"
    assert form["description"] == "[b]post[/b]\n\nline & two"  # taken from the snapshot, the textarea is empty in the HTML
    assert form["anon"] == "1" and form["internal"] == "0"  # the checked box replaced its hidden 0


def test_artwork_never_sends_an_empty_description(tmp_path):
    posts = []
    no_snapshot = EDIT_PAGE.replace("wire:snapshot", "data-x")
    with pytest.raises(TrackerError, match="erase the description"):
        get_publisher("bp", Config(trackers={"bp": web_tracker()}), web_transport(posts, edit=no_snapshot)).set_artwork("777", "u1", "u2")
    assert posts == []


def test_artwork_errors_are_explained(tmp_path):
    with pytest.raises(TrackerError, match="no web session cookie"):
        get_publisher("bp", Config(trackers={"bp": tracker()}), web_transport([])).set_artwork("777", "u1", "u2")
    expired = httpx.MockTransport(lambda r: httpx.Response(302, headers={"location": "https://bp.example/login"}))
    with pytest.raises(LoginError, match="web session cookie is expired"):
        get_publisher("bp", Config(trackers={"bp": web_tracker()}), expired).set_artwork("777", "u1", "u2")
    with pytest.raises(TrackerError, match="no cover field"):
        get_publisher("bp", Config(trackers={"bp": web_tracker()}), web_transport([], edit="<html>forbidden</html>")).set_artwork("777", "u1", "u2")
    back = httpx.Response(302, headers={"location": "https://bp.example/torrents/777/edit"})
    gets = []

    def h(r):
        if r.method == "POST":
            return back
        gets.append(1)  # the first page is the clean form, the one shown after the bounce carries the error
        return httpx.Response(200, text=EDIT_PAGE if len(gets) == 1 else EDIT_PAGE + '<div class="alert">The cover url must be a valid URL.</div>')
    with pytest.raises(TrackerError, match="rejected: The cover url must be a valid URL"):
        get_publisher("bp", Config(trackers={"bp": web_tracker()}), httpx.MockTransport(h)).set_artwork("777", "u1", "u2")


def test_login_test_reports_the_web_session():
    assert "works" in get_publisher("bp", Config(trackers={"bp": web_tracker()}), web_transport([])).fetch_form().note
    assert "No web session cookie" in pub().fetch_form().note
    expired = httpx.MockTransport(lambda r: httpx.Response(200, json={"data": []}) if r.url.path.startswith("/api/") else httpx.Response(302, headers={"location": "/login"}))
    assert "expired" in get_publisher("bp", Config(trackers={"bp": web_tracker()}), expired).fetch_form().note


def test_artwork_is_cut_from_the_promo_image_or_a_frame(tmp_path):
    import io

    from PIL import Image

    from torrentbakery.media.artwork import make_artwork
    buf = io.BytesIO()
    Image.new("RGB", (1920, 1080), (200, 30, 30)).save(buf, "JPEG")
    seen = []

    def h(r):
        seen.append(dict(r.headers))
        return httpx.Response(200, content=buf.getvalue())
    cfg = Config()
    cfg.stash.url, cfg.stash.api_key = "http://stash:9999", "STASHKEY"
    out = tmp_path / "bake"
    out.mkdir()
    poster, banner = make_artwork(cfg, out, {"release": {"cover_url": "http://stash:9999/scene/1/screenshot"}}, httpx.MockTransport(h))
    assert Image.open(poster).size == (400, 600) and Image.open(banner).size == (960, 540)
    assert seen[0]["apikey"] == "STASHKEY"  # Stash images need the key
    make_artwork(cfg, out, {"release": {"cover_url": "https://other.example/x.jpg"}}, httpx.MockTransport(h))
    assert "apikey" not in seen[1]  # but nobody else gets it
    with pytest.raises(FileNotFoundError):
        make_artwork(cfg, out, {"release": {}, "content_path": str(tmp_path / "missing.mp4")})


def test_publish_sets_artwork_and_reports_failures_without_failing(baked, monkeypatch):  # noqa: F811
    import torrentbakery.publish as pm
    from torrentbakery.hosts.base import Uploaded
    uploaded = []

    class Host:
        def upload(self, path, nsfw=True):
            uploaded.append((path.name, nsfw))
            return Uploaded(f"https://imghost.dev/{path.name}", "", "")
    monkeypatch.setattr(pm, "make_artwork", lambda cfg, out, meta, transport=None: (out / "poster.jpg", out / "banner.jpg"))
    monkeypatch.setattr(pm, "get_uploader", lambda name, cfg, transport=None: Host())
    posts = []

    def t(r):
        if r.url.path.startswith("/api") or r.url.path.startswith("/torrent/"):
            return transport([]).handler(r)
        return web_transport(posts).handler(r)
    meta = json.loads((baked / "release.json").read_text())
    meta["image_host"] = "imghost.dev"
    (baked / "release.json").write_text(json.dumps(meta))
    cfg = Config(trackers={"bp": web_tracker()})
    out = publish(cfg, baked, "bp", transport=httpx.MockTransport(t), tagmap=TagMap.load(baked / "none.toml"))
    assert out["artwork"]["ok"] and out["artwork"]["cover_url"] == "https://imghost.dev/poster.jpg" and out["artwork"]["banner_url"] == "https://imghost.dev/banner.jpg"
    assert uploaded == [("poster.jpg", True), ("banner.jpg", True)] and b"cover_url=https%3A%2F%2Fimghost.dev%2Fposter.jpg" in posts[0].content
    assert json.loads((baked / "publish.json").read_text())["bp"]["artwork"]["ok"]
    # without the cookie the upload still succeeds and says what is missing
    out = publish(Config(trackers={"bp": tracker()}), baked, "bp", transport=transport([]), tagmap=TagMap.load(baked / "none.toml"))
    assert out["torrent_id"] == "777" and not out["artwork"]["ok"] and "no web session cookie" in out["artwork"]["message"]
    # and without an image host there is nothing to link
    meta.pop("image_host")
    (baked / "release.json").write_text(json.dumps(meta))
    out = publish(cfg, baked, "bp", transport=httpx.MockTransport(t), tagmap=TagMap.load(baked / "none.toml"))
    assert not out["artwork"]["ok"] and "no image-host upload" in out["artwork"]["message"]
