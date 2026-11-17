import json

import httpx
from fastapi.testclient import TestClient

from torrentbakery import config as config_mod
from torrentbakery.config import Config, QbitCfg, Tracker
from torrentbakery.web.app import create_app
from torrentbakery.web.jobs import JobManager

from test_publish import UPLOAD_PAGE
from test_web import wait_done

COOKIE = "session=SECRETCOOKIE"
PASSKEY = "https://tr.example/PASSKEY123/announce"


def fake_bake(cfg, source, out, **kw):
    d = out / "Scene"
    d.mkdir(parents=True, exist_ok=True)
    (d / "post.bbcode").write_text("[b]post[/b]")
    (d / "Scene.torrent").write_bytes(b"x")
    (d / "release.json").write_text(json.dumps({"release": {"title": "Scene", "tags": ["Blowjob", "Zork Thing"]}, "media": {"height": 1080, "width": 1920},
                                                "uploads": {"cover": {"direct_url": "https://img/c.jpg"}}, "image_host": "pixhost.to"}))
    return d


def setup(tmp_path, publisher=None):
    cfg_path = tmp_path / "config.toml"
    config_mod.save(Config(trackers={"emp": Tracker([PASSKEY], site="empornium", base_url="https://tr.example", cookie=COOKIE, category="2")},
                           qbit=QbitCfg("http://qb", "u", "QBITPASS")), cfg_path)
    tr = httpx.MockTransport(lambda r: httpx.Response(200, text=UPLOAD_PAGE))
    kw = {"publisher": publisher} if publisher else {}
    c = TestClient(create_app(manager=JobManager(tmp_path / "out", cfg_path, fake_bake, **kw), config_path=cfg_path, transport=tr))
    jid = c.post("/jobs", data={"source": "1", "tracker": "emp"}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    wait_done(c, jid)
    return c, cfg_path, jid


def test_secrets_never_rendered_and_blank_keeps(tmp_path):
    c, cfg_path, _ = setup(tmp_path)
    for page in (c.get("/settings").text, c.get("/").text):
        assert COOKIE not in page and "SECRETCOOKIE" not in page and "PASSKEY123" not in page and "QBITPASS" not in page
    assert "cookie saved" in c.get("/settings").text and "empornium" in c.get("/settings").text
    c.post("/settings/tracker", data={"name": "emp", "announce": "", "private": "1", "site": "empornium", "base_url": "https://tr.example/", "cookie": "", "category": "3", "auto_publish": "1"})
    t = config_mod.load(cfg_path).trackers["emp"]
    assert t.cookie == COOKIE and t.announce == [PASSKEY] and t.category == "3" and t.auto_publish and t.base_url == "https://tr.example"
    c.post("/settings/qbit", data={"url": "http://qb2/", "username": "u", "password": "", "start": "1", "path_map": "/a=/b\nnonsense"})
    q = config_mod.load(cfg_path).qbit
    assert q.password == "QBITPASS" and q.url == "http://qb2" and q.path_map == [("/a", "/b")]


def test_tracker_test_and_fetch_announce(tmp_path):
    c, cfg_path, _ = setup(tmp_path)
    r = c.post("/settings/tracker/emp/test").json()
    assert r["ok"] and ["1", "Amateur"] in r["categories"]
    config_mod.save(config_mod.Config(trackers={"emp": Tracker([], site="empornium", base_url="https://tr.example", cookie=COOKIE)}), cfg_path)
    assert c.post("/settings/tracker/emp/announce", follow_redirects=False).status_code == 303
    assert config_mod.load(cfg_path).trackers["emp"].announce == ["https://tr.example/PASS123/announce"]


def test_preview_and_manual_publish(tmp_path):
    seen = []

    def publisher(cfg, out_dir, progress=None, **kw):
        seen.append(kw)
        progress("Uploading to emp", 0.5)
        return {"torrent_url": "https://tr.example/torrents.php?id=9", "qbit": {"ok": True, "message": "added and started"}}

    c, cfg_path, jid = setup(tmp_path, publisher)
    pv = c.get(f"/jobs/{jid}/publish/preview", params={"tracker": "emp"}).json()
    assert pv["ok"] and pv["title"] == "Scene [1080p]" and "blowjob" in pv["tags"] and pv["suggestions"] == {"Zork Thing": "zork.thing"} and ["2", "Studio"] in pv["categories"]
    assert 'id="pub-form"' in c.get(f"/jobs/{jid}").text
    assert c.post(f"/jobs/{jid}/publish", data={"tracker": "emp", "title": "T", "category": "2", "tags": "a b"}, follow_redirects=False).status_code == 303
    for _ in range(100):
        s = c.get(f"/jobs/{jid}/publish/state").json()
        if s["status"] == "done":
            break
    assert s["torrent_url"].endswith("id=9") and seen[0]["title"] == "T" and seen[0]["tags"] == ["a", "b"] and seen[0]["name"] == "emp"
    assert c.post(f"/jobs/{jid}/publish", data={"tracker": "nope"}).status_code == 400


def test_teaching_a_tag_changes_the_next_preview(tmp_path):
    c, cfg_path, jid = setup(tmp_path)
    assert c.post(f"/jobs/{jid}/tags", data={"stash_tag": "Zork Thing", "tracker_tags": "zork", "tracker": "emp"}).json()["ok"]
    pv = c.get(f"/jobs/{jid}/publish/preview", params={"tracker": "emp"}).json()
    assert "zork" in pv["tags"].split() and pv["suggestions"] == {}
    c.post(f"/jobs/{jid}/tags", data={"stash_tag": "Blowjob", "ignore": "1"})
    assert "blowjob" not in c.get(f"/jobs/{jid}/publish/preview", params={"tracker": "emp"}).json()["tags"].split()
    assert "Zork Thing" in c.get("/settings").text and "never sent" in c.get("/settings").text


def test_publish_failure_is_shown_and_auto_publish_error_file(tmp_path):
    def boom(cfg, out_dir, progress=None, **kw):
        raise RuntimeError("cookie expired")
    c, _, jid = setup(tmp_path, boom)
    c.post(f"/jobs/{jid}/publish", data={"tracker": "emp"})
    for _ in range(100):
        if c.get(f"/jobs/{jid}/publish/state").json()["status"] == "error":
            break
    assert "cookie expired" in c.get(f"/jobs/{jid}/publish/state").json()["error"] and "cookie expired" in c.get(f"/jobs/{jid}").text


def test_edit_data_has_no_secrets(tmp_path):
    c, _, _ = setup(tmp_path)
    page = c.get("/settings").text
    data = json.loads(page.split('id="tracker-data" type="application/json">')[1].split("</script>")[0])
    assert data["emp"]["site"] == "empornium" and data["emp"]["category"] == "2" and data["emp"]["base_url"] == "https://tr.example"
    assert "cookie" not in data["emp"] and "announce" not in data["emp"] and "SECRETCOOKIE" not in page and "PASSKEY123" not in page


def test_qbit_test_uses_the_unsaved_form(tmp_path):
    c, cfg_path, _ = setup(tmp_path)  # transport answers any GET with the tracker page; the qBit client only needs a 200 on app/version
    r = c.post("/settings/qbit/test", data={"url": "http://10.10.1.101:50001", "username": "", "password": ""}).json()
    assert r["ok"] and "not saved yet" in r["message"]
    assert config_mod.load(cfg_path).qbit.url == "http://qb"  # testing does not save
    blank = c.post("/settings/qbit/test", data={}).json()  # no form values: the saved connection
    assert blank["ok"] and "not saved yet" not in blank["message"]


def test_seed_again_route(tmp_path):
    from torrentbakery.web.jobs import JobManager as JM
    posts = []
    # a published bake with a real torrent the tracker serves
    cfg_path = tmp_path / "config.toml"
    config_mod.save(Config(trackers={"emp": Tracker([PASSKEY], site="empornium", base_url="https://tr.example", cookie="session=abc", category="2")},
                           qbit=QbitCfg("http://qb")), cfg_path)
    import test_publish as tp
    from torf import Torrent
    media = tmp_path / "share" / "Scene.mp4"
    media.parent.mkdir()
    media.write_bytes(b"x" * 40000)
    t = Torrent(path=media)
    t.trackers = ["https://tr.example/PASS123/announce"]
    t.generate()
    served = tmp_path / "served.torrent"
    t.write(served)
    out_root = tmp_path / "out"
    d = out_root / "Scene"
    d.mkdir(parents=True)
    t.write(d / "Scene.torrent")
    (d / "post.bbcode").write_text("x")
    (d / "release.json").write_text(json.dumps({"release": {"title": "Scene"}, "media": {}, "uploads": {}, "folder_path": str(media.parent), "content_path": str(media)}))
    (d / "publish.json").write_text(json.dumps({"emp": {"torrent_id": "4711", "qbit": {"ok": False, "message": "old"}}}))
    mgr = JM(out_root, cfg_path, lambda *a, **k: d)
    tr = tp.transport(posts, download=served.read_bytes())
    qb = httpx.MockTransport(lambda r: httpx.Response(200, text="v5"))  # no login needed
    c = TestClient(create_app(manager=mgr, config_path=cfg_path, transport=tr, qbit_transport=qb))
    jid = c.post("/jobs", data={"source": "1", "tracker": "emp"}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    wait_done(c, jid)
    assert "Add to qBittorrent again" in c.get(f"/jobs/{jid}").text
    r = c.post(f"/jobs/{jid}/seed", data={"tracker": "emp"}).json()
    assert r["ok"] and "tracker's torrent" in r["message"]
    assert json.loads((d / "publish.json").read_text())["emp"]["qbit"]["ok"] is True
    assert c.post(f"/jobs/{jid}/seed", data={"tracker": "nope"}).status_code == 400
