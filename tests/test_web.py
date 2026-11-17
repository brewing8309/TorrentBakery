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
    assert c.post("/settings/tracker", data={"name": "main", "announce": "https://t/a\nhttps://t/b", "source": "S", "private": "1"}, follow_redirects=False).status_code == 303
    c.post("/settings/stash", data={"url": "http://stash:9999/", "api_key": "SECRETKEY"})
    c.post("/settings/map", data={"stash_path": "/data", "local_path": "/mnt"})
    c.post("/settings/roots", data={"roots": "/mnt/a\n/mnt/b"})
    cfg = config_mod.load(cfg_path)
    assert cfg.trackers["main"].announce == ["https://t/a", "https://t/b"] and cfg.stash.url == "http://stash:9999"
    assert cfg.path_map == [("/data", "/mnt")] and cfg.web_media_roots == ["/mnt/a", "/mnt/b"]
    page = c.get("/settings").text
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
    assert "Download ZIP" in page and "[spoiler=MediaInfo]" in page and "contactsheet.jpg" in page
    assert c.get(f"/jobs/{job_id}/files/post.bbcode").status_code == 200
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
