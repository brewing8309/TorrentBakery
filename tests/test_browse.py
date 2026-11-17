import os

import pytest
from fastapi.testclient import TestClient
from torf import Torrent

from torrentbakery import config as config_mod
from torrentbakery.config import Config
from torrentbakery.media.files import find_main_video
from torrentbakery.pipeline import bake
from torrentbakery.web.app import create_app


@pytest.fixture
def tree(tmp_path):
    root = tmp_path / "media"
    (root / "Release One").mkdir(parents=True)
    (root / "Release One" / "movie.mkv").write_bytes(b"x" * 100)
    (root / "Release One" / "movie.nfo").write_text("nfo")
    (root / "zeta").mkdir()
    (root / "Alpha").mkdir()
    (root / "clip.MP4").write_bytes(b"y" * 10)
    (root / ".hidden").mkdir()
    (root / "notes.txt").write_text("n")
    (tmp_path / "secret").mkdir()
    os.symlink(tmp_path / "secret", root / "escape")
    return tmp_path


def client(tmp_path, roots):
    cfg_path = tmp_path / "config.toml"
    config_mod.save(Config(web_media_roots=[str(r) for r in roots]), cfg_path)
    return TestClient(create_app(tmp_path / "out", cfg_path))


def test_browse_lists_roots_then_dirs_first_only_videos(tree):
    c = client(tree, [tree / "media"])
    top = c.get("/browse").json()
    assert top["roots"] and [e["name"] for e in top["entries"]] == ["media"] and top["parent"] is None
    d = c.get("/browse", params={"path": str(tree / "media")}).json()
    assert [e["name"] for e in d["entries"]] == ["Alpha", "Release One", "zeta", "clip.MP4"]  # dirs first, sorted; .hidden, txt and the escaping symlink left out
    assert d["others"] == 1 and d["parent"] == "" and d["entries"][-1]["type"] == "file" and d["entries"][-1]["size"] == 10
    sub = c.get("/browse", params={"path": str(tree / "media" / "Release One")}).json()
    assert sub["parent"] == str(tree / "media") and [e["name"] for e in sub["entries"]] == ["movie.mkv"]


def test_browse_refuses_paths_outside_the_roots(tree):
    c = client(tree, [tree / "media"])
    assert c.get("/browse", params={"path": str(tree)}).status_code == 403
    assert c.get("/browse", params={"path": str(tree / "media" / ".." / "secret")}).status_code == 403
    assert c.get("/browse", params={"path": str(tree / "media" / "escape")}).status_code == 403  # symlink out of the root
    assert client(tree, []).get("/browse").json()["entries"] == []  # nothing permitted yet


def test_find_main_video_prefers_largest_non_sample(tmp_path):
    (tmp_path / "Sample").mkdir()
    (tmp_path / "Sample" / "sample.mkv").write_bytes(b"s" * 500)
    (tmp_path / "a.mkv").write_bytes(b"a" * 10)
    (tmp_path / "b.mp4").write_bytes(b"b" * 50)
    assert find_main_video(tmp_path).name == "b.mp4"
    only_sample = tmp_path / "x"
    only_sample.mkdir()
    (only_sample / "sample.mkv").write_bytes(b"s")
    assert find_main_video(only_sample).name == "sample.mkv"
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(ValueError):
        find_main_video(empty)


def test_bake_folder_makes_one_torrent_of_the_whole_folder(video, tmp_path):
    folder = tmp_path / "My.Release.2019.1080p"
    folder.mkdir()
    (folder / video.name).write_bytes(video.read_bytes())
    (folder / "info.nfo").write_text("nfo")
    out = bake(folder / video.name, tmp_path / "out", Config(), torrent_path=folder)
    assert out.name == "My.Release.2019.1080p"  # dots in a folder name are not cut off like a file extension
    t = Torrent.read(next(out.glob("*.torrent")))
    assert t.name == "My.Release.2019.1080p" and sorted(f.name for f in t.files) == ["info.nfo", video.name]
    assert (out / "contactsheet.jpg").exists() and "test clip.mp4" in (out / "post.bbcode").read_text()


def test_web_job_with_folder_and_media_root_check(video, tmp_path):
    import time
    folder = tmp_path / "media" / "Rel"
    folder.mkdir(parents=True)
    (folder / video.name).write_bytes(video.read_bytes())
    c = client(tmp_path, [tmp_path / "media"])
    jid = c.post("/jobs", data={"source": str(folder)}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    for _ in range(300):
        s = c.get(f"/jobs/{jid}/state").json()
        if s["status"] in ("done", "error"):
            break
        time.sleep(0.1)
    assert s["status"] == "done", s
    other = tmp_path / "other"
    other.mkdir()
    jid2 = c.post("/jobs", data={"source": str(other)}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    for _ in range(100):
        s = c.get(f"/jobs/{jid2}/state").json()
        if s["status"] != "queued" and s["status"] != "running":
            break
        time.sleep(0.1)
    assert s["status"] == "error" and "media roots" in s["error"]
