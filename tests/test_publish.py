import json

import httpx
import pytest
from torf import Torrent

from torrentbakery.clients import QBittorrent, QbitError, map_save_path
from torrentbakery.config import Config, QbitCfg, Tracker
from torrentbakery.publish import performer_tags, prepare, publish, render_title
from torrentbakery.tags import TagMap, empify
from torrentbakery.trackers import LoginError, PublishRequest, TrackerError, get_publisher

UPLOAD_PAGE = """<html><body>
<input type="text" value="https://tr.example/PASS123/announce" readonly>
<form class="create_form" action="" enctype="multipart/form-data" method="post" id="upload_table">
<input type="hidden" name="submit" value="true"><input type="hidden" name="auth" value="AUTHTOKEN">
<select id="categories" name="type"><option value="">---</option><option value="1">Amateur</option><option value="2">Studio</option></select>
<input type="file" id="file" name="file_input">
<input type="text" id="title" name="title"><input type="text" id="image" name="image">
<input type="text" id="taginput" name="taglist"><textarea id="desc" name="desc">
</textarea>
<input type="radio" name="anonymous" value="0" checked> <input type="radio" name="anonymous" value="1">
<input type="submit" value="Upload torrent">
</form></body></html>"""


def tracker(**kw):
    return Tracker(["https://tr.example/PASS123/announce"], "", True, site="empornium", base_url="https://tr.example", cookie="session=abc", category="2", **kw)


def req(tmp_path, **kw):
    t = tmp_path / "x.torrent"
    t.write_bytes(b"d4:infod4:name1:xee")
    return PublishRequest(t, "Title", "[b]post[/b]", ["a", "b.c"], "https://img/c.jpg", **kw)


TORRENT_PAGE = '<a href="torrents.php?action=download&amp;id=4711&amp;authkey=AK&amp;torrent_pass=TP">DL</a><a href="torrents.php?action=download&amp;id=1&amp;authkey=AK">other</a>'


def transport(posts, page=UPLOAD_PAGE, post_response=None, download=None, torrent_page=TORRENT_PAGE, gets=None):
    def h(r):
        assert r.headers["cookie"] == "session=abc"
        if r.method == "GET":
            if gets is not None:
                gets.append(str(r.url))
            if "action=download" in str(r.url):
                return httpx.Response(200, content=download if download is not None else b"<html>nope</html>")
            if r.url.path.endswith("torrents.php"):
                return httpx.Response(200, text=torrent_page)
            return httpx.Response(200, text=page)
        posts.append(r)
        return post_response or httpx.Response(302, headers={"location": "torrents.php?id=4711"})
    return httpx.MockTransport(h)


def test_fetch_form_reads_categories_and_announce():
    f = get_publisher("emp", Config(trackers={"emp": tracker()}), transport([])).fetch_form()
    assert f.categories == [("1", "Amateur"), ("2", "Studio")] and f.announce.endswith("/PASS123/announce") and f.fields["auth"] == "AUTHTOKEN"


def test_publish_posts_filled_form(tmp_path):
    posts = []
    res = get_publisher("emp", Config(trackers={"emp": tracker(anonymous=True)}), transport(posts)).publish(req(tmp_path))
    assert res.torrent_id == "4711" and res.torrent_url == "https://tr.example/torrents.php?id=4711"
    body = posts[0].content
    for needle in (b'name="auth"', b"AUTHTOKEN", b'name="title"', b"Title", b'name="taglist"', b"a b.c", b'name="desc"', b"[b]post[/b]",
                   b'name="image"', b"https://img/c.jpg", b'name="type"', b'name="file_input"; filename="x.torrent"', b"application/x-bittorrent"):
        assert needle in body, needle
    assert b'name="anonymous"\r\n\r\n1' in body and b'name="anonymous"\r\n\r\n0' not in body


def test_dry_run_sends_nothing_and_hides_auth(tmp_path):
    posts = []
    res = get_publisher("emp", Config(trackers={"emp": tracker()}), transport(posts)).publish(req(tmp_path), dry_run=True)
    assert posts == [] and res.dry_run and res.sent["title"] == "Title" and "auth" not in res.sent and res.sent["file_input"] == "x.torrent"


def test_femdomcult_tags_field(tmp_path):
    page = UPLOAD_PAGE.replace('id="taginput" name="taglist"', 'id="tags" name="tags"')
    t = tracker()
    t.site = "femdomcult"
    posts = []
    get_publisher("fc", Config(trackers={"fc": t}), transport(posts, page)).publish(req(tmp_path))
    assert b'name="tags"' in posts[0].content


def test_login_page_and_redirect(tmp_path):
    def h(r):
        return httpx.Response(302, headers={"location": "login.php"})
    with pytest.raises(LoginError):
        get_publisher("emp", Config(trackers={"emp": tracker()}), httpx.MockTransport(h)).fetch_form()
    with pytest.raises(LoginError):
        get_publisher("emp", Config(trackers={"emp": tracker()}), transport([], page="<form><input type=password name=password></form> login")).fetch_form()


def test_rejection_shows_tracker_message(tmp_path):
    bad = httpx.Response(200, text='<div class="error_message">Torrent already exists</div>')
    with pytest.raises(TrackerError, match="already exists"):
        get_publisher("emp", Config(trackers={"emp": tracker()}), transport([], post_response=bad)).publish(req(tmp_path))


def test_bad_category_and_missing_cookie(tmp_path):
    t = tracker()
    t.category = "99"
    with pytest.raises(TrackerError, match="not offered"):
        get_publisher("emp", Config(trackers={"emp": t}), transport([])).publish(req(tmp_path))
    t.cookie = ""
    with pytest.raises(ValueError):
        get_publisher("emp", Config(trackers={"emp": t}))


def test_tagmap(tmp_path):
    m = TagMap.load(tmp_path / "none.toml")
    assert empify("Face Fuck!") == "face.fuck"
    tags, sugg = m.map_tags(["Blowjob", "Brown Hair", "60 FPS", "Zork Thing", "blowjob"], "emp")
    assert tags[:3] == ["blowjob", "brunette", "brown.hair"] and "zork.thing" in tags and sugg == {"Zork Thing": "zork.thing"} and tags.count("blowjob") == 1


def test_tagmap_user_overrides(tmp_path):
    from torrentbakery.tags import save_user_map
    f = tmp_path / "tags.toml"
    save_user_map({"tags": {"Zork Thing": "zork"}, "tracker": {"emp": {"Blowjob": "bj"}}, "ignored": ["Anal"]}, f)
    m = TagMap.load(f)
    assert m.map_tag("Zork Thing") == (["zork"], True) and m.map_tag("Blowjob", "emp")[0] == ["bj"] and m.map_tag("Blowjob", "hf")[0] == ["blowjob"]
    assert m.map_tag("Anal") == ([], True)


MEDIA = {"height": 1080, "width": 1920, "video_codec": "h264", "fps": 29.97, "duration": 60}
REL = {"title": "Scene", "date": "2024-05-17", "studio": "Studio", "performers": ["Jane Doe", "Eve"], "tags": ["Blowjob"]}


def test_title_and_performer_tags():
    assert render_title("", REL, MEDIA) == "[Studio] Jane Doe & Eve - Scene (2024-05-17) [1080p]"
    assert render_title("{{ title }}|{{ resolution }}", REL, MEDIA) == "Scene|1080p"
    assert performer_tags(REL) == ["jane.doe", "eve"]  # metadata (date, resolution, codec, fps) is deliberately not tagged


@pytest.fixture
def baked(tmp_path):
    """A bake folder without running ffmpeg: a real torrent over a small media file plus the metadata files."""
    media = tmp_path / "share" / "Scene.mp4"
    media.parent.mkdir()
    media.write_bytes(b"x" * 40000)
    out = tmp_path / "out" / "Scene"
    out.mkdir(parents=True)
    t = Torrent(path=media)
    t.trackers = ["https://other.example/announce"]
    t.generate()
    t.write(out / "Scene.torrent")
    (out / "post.bbcode").write_text("[b]post[/b]")
    (out / "release.json").write_text(json.dumps({"release": REL, "media": MEDIA, "uploads": {"cover": {"direct_url": "https://img/c.jpg"}},
                                                  "folder_path": str(media.parent), "content_path": str(media)}))
    return out


def test_prepare_rebuilds_torrent_for_tracker(baked):
    cfg = Config(trackers={"emp": tracker()})
    p = prepare(cfg, baked, "emp", tagmap=TagMap.load(baked / "none.toml"))
    t = Torrent.read(p.torrent)
    assert p.torrent.name == "Scene.tracker.emp.torrent" and t.source == "Emp" and t.private and t.trackers[0][0].endswith("/PASS123/announce")
    assert p.title.startswith("[Studio]") and p.tags == ["jane.doe", "eve", "blowjob"] and p.image == "https://img/c.jpg"
    assert Torrent.read(baked / "Scene.torrent").infohash != t.infohash  # the source flag changes the hash, so the base torrent stays untouched


def tracker_version(baked):
    """The torrent as the tracker serves it: same files, but it rewrote the source flag, so the infohash differs from ours."""
    t = Torrent.read(baked / "Scene.torrent")
    t.source = "HFtracker"
    t.private = True
    t.write(baked.parent / "served.torrent")  # outside the bake folder: it must not look like a bake's own torrent
    return (baked.parent / "served.torrent").read_bytes(), t.infohash


def test_publish_end_to_end_with_qbit(baked):
    posts, qbit, gets = [], [], []
    served, served_hash = tracker_version(baked)

    def q(r):  # a qBittorrent that wants a login: everything but the login answers 403 until it set the session cookie
        qbit.append((r.url.path, r.content))
        if r.url.path.endswith("login"):
            return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=1; Path=/"})
        return httpx.Response(200, text="") if r.headers.get("cookie") else httpx.Response(403)
    cfg = Config(trackers={"emp": tracker()}, qbit=QbitCfg("http://qb:8080", "u", "p", "tb", True, [(str(baked.parent.parent / "share"), "/data")]))
    out = publish(cfg, baked, "emp", transport=transport(posts, download=served, gets=gets), qbit_transport=httpx.MockTransport(q), tagmap=TagMap.load(baked / "none.toml"))
    assert out["torrent_id"] == "4711" and out["qbit"]["ok"] and "tracker changed the torrent" in out["qbit"]["message"]
    assert out["infohash"]["tracker"] == served_hash != out["infohash"]["local"]
    paths = [p for p, _ in qbit]
    assert paths == ["/api/v2/app/version", "/api/v2/auth/login", "/api/v2/torrents/add", "/api/v2/torrents/recheck", "/api/v2/torrents/start"]
    assert served in qbit[2][1] and b"/data" in qbit[2][1] and b'name="category"' in qbit[2][1]  # the tracker's file, not ours
    assert served_hash.encode() in qbit[3][1]  # rechecked under the tracker's hash
    assert any("authkey=AK&torrent_pass=TP" in g and "id=4711" in g for g in gets)  # the link of this torrent, not the "other" one
    assert (baked / "Scene.tracker.emp.downloaded.torrent").read_bytes() == served
    assert json.loads((baked / "publish.json").read_text())["emp"]["torrent_id"] == "4711"
    # the next publish still picks the right base torrent
    assert prepare(cfg, baked, "emp", tagmap=TagMap.load(baked / "none.toml"), build_torrent=False).torrent.name == "Scene.tracker.emp.torrent"


def test_publish_survives_qbit_failure(baked):
    cfg = Config(trackers={"emp": tracker()}, qbit=QbitCfg("http://qb:8080", "u", "p"))
    served, _ = tracker_version(baked)
    out = publish(cfg, baked, "emp", transport=transport([], download=served), qbit_transport=httpx.MockTransport(lambda r: httpx.Response(200, text="Fails.") if r.url.path.endswith("login") else httpx.Response(403)),
                  tagmap=TagMap.load(baked / "none.toml"))
    assert out["torrent_id"] == "4711" and not out["qbit"]["ok"] and "login failed" in out["qbit"]["message"]


def test_publish_needs_cover_url_but_dry_run_does_not(baked):
    meta = json.loads((baked / "release.json").read_text())
    meta["uploads"] = {}
    (baked / "release.json").write_text(json.dumps(meta))
    cfg = Config(trackers={"emp": tracker()})
    with pytest.raises(TrackerError, match="image"):
        publish(cfg, baked, "emp", transport=transport([]))
    assert publish(cfg, baked, "emp", dry_run=True, transport=transport([]))["dry_run"]


def test_qbit_helpers():
    assert map_save_path("/mnt/x/a", [("/mnt/x", "/data")]) == "/data/a" and map_save_path("/other", [("/mnt/x", "/data")]) == "/other"
    with pytest.raises(QbitError):
        QBittorrent(QbitCfg())


def test_bake_source_auto_publish_hook(tmp_path, monkeypatch):
    from torrentbakery import service
    media = tmp_path / "a.mp4"
    media.write_bytes(b"x")
    out = tmp_path / "out" / "a"
    out.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(service, "bake", lambda *a, **k: out)
    monkeypatch.setattr(service, "publish_release", lambda cfg, d, name, **k: calls.append(name))
    t = tracker()
    cfg = Config(trackers={"emp": t, "plain": Tracker(["http://x/announce"])})
    service.bake_source(cfg, str(media), tmp_path / "out", tracker="emp")  # auto_publish off: nothing happens
    service.bake_source(cfg, str(media), tmp_path / "out", tracker="plain", publish=True)  # no site: nothing happens
    assert calls == []
    service.bake_source(cfg, str(media), tmp_path / "out", tracker="emp", publish=True)
    t.auto_publish = True
    service.bake_source(cfg, str(media), tmp_path / "out", tracker="emp")
    service.bake_source(cfg, str(media), tmp_path / "out", tracker="emp", publish=False)
    assert calls == ["emp", "emp"] and not (out / "publish-error.txt").exists()

    def boom(*a, **k):
        raise TrackerError("cookie expired")
    monkeypatch.setattr(service, "publish_release", boom)
    assert service.bake_source(cfg, str(media), tmp_path / "out", tracker="emp") == out  # the bake still succeeds
    assert "cookie expired" in (out / "publish-error.txt").read_text()


def test_performers_always_tagged_even_with_hand_edited_tags(baked):
    cfg = Config(trackers={"emp": tracker()})
    p = prepare(cfg, baked, "emp", tags=["only", "this", "eve"], tagmap=TagMap.load(baked / "none.toml"), build_torrent=False)
    assert p.tags == ["jane.doe", "eve", "only", "this"]


def test_male_performers_are_left_out_of_title_and_tags():
    rel = {**REL, "performers": ["Jane Doe", "Toby Dick", "Eve"], "male_performers": ["Toby Dick"]}
    assert render_title("", rel, MEDIA) == "[Studio] Jane Doe & Eve - Scene (2024-05-17) [1080p]"
    assert performer_tags(rel) == ["jane.doe", "eve"]
    from torrentbakery.render.post import release_tags
    assert release_tags({**rel, "tags": ["Blowjob"]}) == "jane.doe eve blowjob"
    only_male = {**REL, "performers": ["Toby Dick"], "male_performers": ["Toby Dick"]}
    assert render_title("", only_male, MEDIA) == "[Studio] Scene (2024-05-17) [1080p]" and performer_tags(only_male) == []


HF_PAGE = UPLOAD_PAGE.replace('''<input type="file" id="file" name="file_input">''', '''<input type="file" id="file" name="file_input">
<input type="submit" name="checkonly" value="Load &amp; check torrent">''').replace(
    '<input type="submit" value="Upload torrent">', '<textarea id="mediainfo" name="mediainfo"></textarea><input type="submit" name="submit" id="post" value="Upload torrent">')


def test_form_with_check_button_posts_the_upload_button_and_mediainfo(tmp_path):
    posts = []
    r = req(tmp_path)
    r.mediainfo = "General\nFormat : MPEG-4"
    get_publisher("emp", Config(trackers={"emp": tracker()}), transport(posts, HF_PAGE)).publish(r)
    body = posts[0].content
    assert b'name="submit"\r\n\r\nUpload torrent' in body and b"checkonly" not in body
    assert b'name="mediainfo"\r\n\r\nGeneral' in body


def test_form_returned_without_message_gets_a_hint(tmp_path):
    page = httpx.Response(200, text=UPLOAD_PAGE)
    with pytest.raises(TrackerError, match="upload form again"):
        get_publisher("emp", Config(trackers={"emp": tracker()}), transport([], post_response=page)).publish(req(tmp_path))


def test_qbit_without_login_when_the_address_is_whitelisted(tmp_path):
    seen = []

    def h(r):
        seen.append(r.url.path)
        if r.url.path.endswith("auth/login"):
            return httpx.Response(204)
        return httpx.Response(200, text="v5.3.0")
    assert QBittorrent(QbitCfg("http://qb:50001"), httpx.MockTransport(h)).test() == "qBittorrent v5.3.0"
    assert "/api/v2/auth/login" not in seen  # no credentials needed, none sent

    def locked(r):
        if r.url.path.endswith("app/version") and r.method == "GET" and not r.headers.get("cookie"):
            return httpx.Response(403)
        return httpx.Response(200, text="Ok.", headers={"set-cookie": "SID=1; Path=/"}) if r.url.path.endswith("login") else httpx.Response(200, text="v5")
    assert QBittorrent(QbitCfg("http://qb", "u", "p"), httpx.MockTransport(locked)).test() == "qBittorrent v5"


def test_download_builds_the_link_when_the_page_has_none(tmp_path):
    gets = []
    pub = get_publisher("emp", Config(trackers={"emp": tracker()}), transport([], download=b"d4:infod4:name1:xee", torrent_page="<p>nothing</p>", gets=gets))
    assert pub.download_torrent("4711").startswith(b"d4:info")
    assert any("action=download" in g and "id=4711" in g and "authkey=AUTHTOKEN" in g and "torrent_pass=PASS123" in g for g in gets)  # auth from the form, passkey from the announce URL


def test_download_failures(tmp_path):
    pub = get_publisher("emp", Config(trackers={"emp": tracker()}), transport([], download=None))
    pub.retry_delay = 0
    with pytest.raises(TrackerError, match="did not return a torrent"):
        pub.download_torrent("4711")

    def dead(r):
        return httpx.Response(302, headers={"location": "/login"})
    with pytest.raises(LoginError):
        get_publisher("emp", Config(trackers={"emp": tracker()}), httpx.MockTransport(dead)).download_torrent("4711")


def test_publish_reports_when_the_tracker_torrent_cannot_be_fetched(baked):
    qbit = []
    cfg = Config(trackers={"emp": tracker()}, qbit=QbitCfg("http://qb:8080"))
    import torrentbakery.trackers.gazelle as gz
    old = gz.GazellePublisher.retry_delay
    gz.GazellePublisher.retry_delay = 0
    try:
        out = publish(cfg, baked, "emp", transport=transport([]), qbit_transport=httpx.MockTransport(lambda r: qbit.append(r.url.path) or httpx.Response(200, text="v5")),
                      tagmap=TagMap.load(baked / "none.toml"))
    finally:
        gz.GazellePublisher.retry_delay = old
    assert out["torrent_id"] == "4711" and not out["qbit"]["ok"] and "nothing added" in out["qbit"]["message"] and not any("torrents/add" in x for x in qbit)
