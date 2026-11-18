import time

import httpx
import pytest
from fastapi.testclient import TestClient

from torrentbakery import config as config_mod
from torrentbakery.config import Config, StashCfg
from torrentbakery.web.app import create_app


@pytest.fixture
def cfg_path(tmp_path):
    return tmp_path / "config.toml"


def wait_done(client, job_id):
    for _ in range(300):
        s = client.get(f"/jobs/{job_id}/state").json()
        if s["status"] in ("done", "error"):
            return s
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_settings_roundtrip_and_key_hidden(tmp_path, cfg_path):
    c = TestClient(create_app(tmp_path / "out", cfg_path))
    assert c.post("/settings/tracker", data={"name": "main", "announce": "https://t.example/PASSKEY/a\nhttps://t.example/PASSKEY/b", "source": "S", "private": "1"}, follow_redirects=False).status_code == 303
    c.post("/settings/stash", data={"url": "http://stash:9999/", "api_key": "SECRETKEY"})
    c.post("/settings/map", data={"stash_path": "/data", "local_path": "/mnt"})
    c.post("/settings/roots", data={"roots": "/mnt/a\n/mnt/b"})
    cfg = config_mod.load(cfg_path)
    assert cfg.trackers["main"].announce == ["https://t.example/PASSKEY/a", "https://t.example/PASSKEY/b"] and cfg.stash.url == "http://stash:9999"
    assert cfg.path_map == [("/data", "/mnt")] and cfg.web_media_roots == ["/mnt/a", "/mnt/b"]
    page = c.get("/settings").text
    assert "PASSKEY" not in page and "t.example" in page  # announce passkeys are never rendered
    assert "SECRETKEY" not in page and "main" in page and "saved" in page
    c.post("/settings/stash", data={"url": "http://stash:9999", "api_key": ""})  # blank keeps the key
    assert config_mod.load(cfg_path).stash.api_key == "SECRETKEY"
    c.post("/settings/tracker/main/delete")
    assert "main" not in config_mod.load(cfg_path).trackers


def test_search_and_thumb(tmp_path, cfg_path):
    config_mod.save(Config(stash=StashCfg("http://stash.example", "k")), cfg_path)

    def h(req):
        assert req.headers["ApiKey"] == "k"
        if req.url.path == "/graphql":
            return httpx.Response(200, json={"data": {"findScenes": {"scenes": [{"id": "5", "title": None, "date": None, "studio": None, "files": [{"basename": "x.mp4"}]}]}}})
        assert req.url.path == "/scene/5/screenshot"
        return httpx.Response(200, content=b"img", headers={"content-type": "image/jpeg"})

    c = TestClient(create_app(tmp_path / "out", cfg_path, transport=httpx.MockTransport(h)))
    assert c.get("/search", params={"q": "abc"}).json() == [{"id": "5", "title": "x.mp4", "date": "", "studio": ""}]
    assert c.get("/stash-thumb/5").content == b"img"


def test_bake_job_end_to_end(tmp_path, cfg_path, video):
    config_mod.save(Config(web_media_roots=[str(video.parent)], trackers={"t": config_mod.Tracker(["https://t/a"], "S")}), cfg_path)
    c = TestClient(create_app(tmp_path / "out", cfg_path))
    r = c.post("/jobs", data={"source": str(video), "tracker": "t"}, follow_redirects=False)
    assert r.status_code == 303
    job_id = r.headers["location"].rsplit("/", 1)[-1]
    assert wait_done(c, job_id)["status"] == "done"
    page = c.get(f"/jobs/{job_id}").text
    assert "Download everything as ZIP" in page and "[spoiler=MediaInfo]" in page and "contactsheet.jpg" in page and "cover.webp" in page
    assert c.get(f"/jobs/{job_id}/files/post.bbcode").status_code == 200
    assert c.get(f"/jobs/{job_id}/files/tags.txt").status_code == 200
    assert c.get(f"/jobs/{job_id}/files/..%2Fconfig.toml").status_code == 404
    assert c.get(f"/jobs/{job_id}/files/nope.txt").status_code == 404
    assert c.get(f"/jobs/{job_id}/zip").headers["content-type"] == "application/zip"


def test_file_outside_media_roots_is_rejected(tmp_path, cfg_path, video):
    config_mod.save(Config(web_media_roots=[str(tmp_path / "elsewhere")]), cfg_path)
    c = TestClient(create_app(tmp_path / "out", cfg_path))
    job_id = c.post("/jobs", data={"source": str(video)}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    assert wait_done(c, job_id)["status"] == "error"
    assert "media roots" in c.get(f"/jobs/{job_id}").text


def test_numeric_source_means_stash_scene(tmp_path, cfg_path):
    calls = []

    def runner(cfg, source, out, **kw):
        calls.append(source)
        return out

    from torrentbakery.web.jobs import JobManager
    c = TestClient(create_app(manager=JobManager(tmp_path / "out", cfg_path, runner)))
    jid = c.post("/jobs", data={"source": "1234"}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    wait_done(c, jid)
    assert calls == ["stash:1234"]


def test_stash_connection_test(tmp_path, cfg_path):
    config_mod.save(Config(stash=StashCfg("http://stash.example", "saved")), cfg_path)
    seen = []

    def h(req):
        seen.append(req.headers.get("ApiKey"))
        if req.url.host == "down.example":
            raise httpx.ConnectError("refused")
        if req.headers.get("ApiKey") == "bad":
            return httpx.Response(401)
        return httpx.Response(200, json={"data": {"version": {"version": "v0.31.1"}, "configuration": {"general": {"stashBoxes": [{"name": "StashDB"}]}}}})

    c = TestClient(create_app(tmp_path / "out", cfg_path, transport=httpx.MockTransport(h)))
    ok = c.post("/settings/stash/test", data={"url": "http://stash.example", "api_key": ""}).json()
    assert ok["ok"] and "v0.31.1" in ok["message"] and "StashDB" in ok["message"] and seen[-1] == "saved"  # blank key falls back to the saved one
    assert "wrong" in c.post("/settings/stash/test", data={"url": "http://stash.example", "api_key": "bad"}).json()["message"]
    assert "not reachable" in c.post("/settings/stash/test", data={"url": "http://down.example", "api_key": "x"}).json()["message"]
    empty = TestClient(create_app(tmp_path / "out2", tmp_path / "none.toml"))
    assert not empty.post("/settings/stash/test", data={"url": "", "api_key": ""}).json()["ok"]


def test_hamsterimg_key_via_settings(tmp_path, cfg_path):
    c = TestClient(create_app(tmp_path / "out", cfg_path))
    c.post("/settings/host/hamsterimg", data={"api_key": "HAMSTERKEY"})
    assert config_mod.load(cfg_path).hosts["hamsterimg"]["api_key"] == "HAMSTERKEY"
    assert "HAMSTERKEY" not in c.get("/settings").text
    c.post("/settings/host/hamsterimg", data={"api_key": ""})
    assert config_mod.load(cfg_path).hosts["hamsterimg"]["api_key"] == "HAMSTERKEY"
    c.post("/settings/host/hamsterimg/delete")
    assert "hamsterimg" not in config_mod.load(cfg_path).hosts
    c.post("/settings/host/imghost.dev", data={"api_key": "IMGKEY"})
    assert config_mod.load(cfg_path).hosts["imghost.dev"]["api_key"] == "IMGKEY" and "IMGKEY" not in c.get("/settings").text
    assert c.post("/settings/host/pixhost.to", data={"api_key": "x"}).status_code == 404


def test_picked_scene_shows_its_name(tmp_path, cfg_path):
    from torrentbakery.web.jobs import JobManager

    def runner(cfg, source, out, **kw):
        out.mkdir(parents=True, exist_ok=True)
        (out / "post.bbcode").write_text("post")
        return out

    c = TestClient(create_app(manager=JobManager(tmp_path / "out", cfg_path, runner)))
    r = c.post("/jobs", data={"source": "stash:1356", "label": "Great Scene · Studio X"}, follow_redirects=False)
    jid = r.headers["location"].rsplit("/", 1)[-1]
    wait_done(c, jid)
    assert "Great Scene · Studio X" in c.get(f"/jobs/{jid}").text and "Great Scene" in c.get("/").text
    assert "stash:1356" not in c.get(f"/jobs/{jid}").text.split("<h1>")[1].split("</h1>")[0]


def test_default_host_preselected(tmp_path, cfg_path):
    c = TestClient(create_app(tmp_path / "out", cfg_path))
    c.post("/settings/default-host", data={"host": "pixhost.cc"})
    assert config_mod.load(cfg_path).default_host == "pixhost.cc"
    assert "<option selected>pixhost.cc</option>" in c.get("/").text
    assert c.post("/settings/default-host", data={"host": "nope"}).status_code == 400


def test_history_survives_restart(tmp_path, cfg_path):
    from torrentbakery.web.jobs import JobManager

    def runner(cfg, source, out, **kw):
        if source == "stash:bad":
            raise ValueError("boom")
        d = out / "Rel"
        d.mkdir(parents=True, exist_ok=True)
        (d / "post.bbcode").write_text("post")
        (d / "release.json").write_text('{"release": {"title": "Baked Title"}}')
        return d

    out = tmp_path / "out"
    c = TestClient(create_app(manager=JobManager(out, cfg_path, runner)))
    ok = c.post("/jobs", data={"source": "stash:1", "tracker": "t"}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    bad = c.post("/jobs", data={"source": "stash:bad"}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    wait_done(c, ok), wait_done(c, bad)

    # a fresh manager on the same output folder = the service restarted
    c2 = TestClient(create_app(manager=JobManager(out, cfg_path, runner)))
    page = c2.get("/history").text
    assert "Baked Title" in page and "stash:bad".split(":")[1] in page and "boom" in page
    assert "Baked Title" in c2.get(f"/jobs/{ok}").text and "boom" in c2.get(f"/jobs/{bad}").text
    assert page.index("bad") < page.index("Baked Title")  # newest first
    (out / "history.jsonl").write_text((out / "history.jsonl").read_text() + "not json\n")
    assert TestClient(create_app(manager=JobManager(out, cfg_path, runner))).get("/history").status_code == 200  # damaged line is skipped


def test_job_page_offers_copyable_tags(tmp_path, cfg_path):
    from torrentbakery.web.jobs import JobManager

    def runner(cfg, source, out, **kw):
        d = out / "Rel"
        d.mkdir(parents=True, exist_ok=True)
        (d / "post.bbcode").write_text("post")
        (d / "release.json").write_text('{"release": {"title": "T", "tags": ["Face Fuck", "Asian", "Big  Tits"]}, "folder_name": "My Folder", "folder_path": "/media/x/My Folder"}')
        return d

    c = TestClient(create_app(manager=JobManager(tmp_path / "out", cfg_path, runner)))
    jid = c.post("/jobs", data={"source": "stash:1"}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    wait_done(c, jid)
    page = c.get(f"/jobs/{jid}").text
    assert '<textarea id="tags" rows="2" readonly>face.fuck asian big.tits</textarea>' in page
    assert '<input type="text" id="folder-name" readonly value="My Folder">' in page and 'value="/media/x/My Folder"' in page
    assert '<input type="text" id="release-title" readonly value="T">' in page and "copyText('release-title'" in page


def test_bake_form_remembers_last_choices(tmp_path, cfg_path):
    import re

    from torrentbakery.config import Tracker
    from torrentbakery.web.jobs import JobManager
    config_mod.save(Config(trackers={"a": Tracker(["http://x/announce"]), "b": Tracker(["http://y/announce"], site="empornium", base_url="http://y")}, default_host="pixhost.cc"), cfg_path)
    c = TestClient(create_app(manager=JobManager(tmp_path / "out", cfg_path, lambda cfg, source, out, **kw: out), config_path=cfg_path))
    page = c.get("/").text
    assert '<option selected>pixhost.cc</option>' in page and "<option selected>a</option>" not in page  # first visit: the default host, no tracker
    jid = c.post("/jobs", data={"source": "1", "tracker": "b", "upload": "imgbox", "cover_source": "stash", "cover_format": "gif", "sfw": "1"}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    wait_done(c, jid)
    page = c.get("/").text
    assert "<option selected>b</option>" in page and "<option selected>imgbox</option>" in page and 'name="publish"' not in page
    assert '<option value="stash" selected>' in page and '<option value="gif" selected>' in page and re.search(r'name="sfw" value="1" checked', page)
    c.post("/jobs", data={"source": "2", "tracker": "", "upload": ""}, follow_redirects=False)  # choosing "none" is remembered as well
    page = c.get("/").text
    assert not re.search(r"<option selected>(a|b)</option>", page) and "<option selected>imgbox</option>" not in page and "<option selected>pixhost.cc</option>" not in page
    assert config_mod.load(cfg_path).last_used["upload"] == "" and config_mod.load(cfg_path).trackers["b"].site == "empornium"
