import threading

from fastapi.testclient import TestClient

from torrentbakery.web.app import create_app
from torrentbakery.web.jobs import JobManager

from test_web import wait_done


def runner(cfg, source, out, **kw):
    d = out / kw.get("name", source.replace("/", "_").replace(":", "_"))
    d.mkdir(parents=True, exist_ok=True)
    (d / "release.json").write_text("{}")
    (d / "x.torrent").write_bytes(b"x")
    (out / f"{d.name}.zip").write_bytes(b"zip")
    return d


def make(tmp_path, run=runner):
    out = tmp_path / "out"
    return out, TestClient(create_app(manager=JobManager(out, tmp_path / "c.toml", run)))


def bake(c, source):
    jid = c.post("/jobs", data={"source": source}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    wait_done(c, jid)
    return jid


def test_delete_one_removes_files_and_history_line(tmp_path):
    out, c = make(tmp_path)
    a, b = bake(c, "/m/a.mp4"), bake(c, "/m/b.mp4")
    assert c.post(f"/jobs/{a}/delete", follow_redirects=False).status_code == 303
    assert not (out / "_m_a.mp4").exists() and not (out / "_m_a.mp4.zip").exists()
    assert (out / "_m_b.mp4" / "x.torrent").exists() and (out / "_m_b.mp4.zip").exists()
    assert c.get(f"/jobs/{a}").status_code == 404 and c.get(f"/jobs/{b}").status_code == 200
    assert a not in (out / "history.jsonl").read_text() and b in (out / "history.jsonl").read_text()
    assert b in {j.id for j in JobManager(out, tmp_path / "c.toml", runner).all()} and a not in {j.id for j in JobManager(out, tmp_path / "c.toml", runner).all()}


def test_entries_sharing_a_folder_go_together(tmp_path):
    out, c = make(tmp_path)
    a1, a2 = bake(c, "/m/a.mp4"), bake(c, "/m/a.mp4")  # a re-bake reuses the folder
    assert "1 other entry" in c.get("/history").text
    c.post(f"/jobs/{a1}/delete")
    assert not (out / "_m_a.mp4").exists() and c.get(f"/jobs/{a2}").status_code == 404


def test_clear_everything(tmp_path):
    out, c = make(tmp_path)
    bake(c, "/m/a.mp4"), bake(c, "/m/b.mp4")
    assert c.post("/history/clear", follow_redirects=False).status_code == 303
    assert [p.name for p in out.iterdir()] == ["history.jsonl"] and (out / "history.jsonl").read_text() == ""
    assert "Nothing baked yet" in c.get("/history").text


def test_running_job_is_not_deleted(tmp_path):
    gate = threading.Event()

    def slow(cfg, source, out, **kw):
        if source.endswith("slow.mp4"):
            gate.wait(10)
        return runner(cfg, source, out, **kw)
    out, c = make(tmp_path, slow)
    done = bake(c, "/m/a.mp4")
    jid = c.post("/jobs", data={"source": "/m/slow.mp4"}, follow_redirects=False).headers["location"].rsplit("/", 1)[-1]
    assert c.post(f"/jobs/{jid}/delete").status_code == 409
    c.post("/history/clear")  # removes the finished one, keeps the running one
    assert c.get(f"/jobs/{done}").status_code == 404 and c.get(f"/jobs/{jid}").status_code == 200
    gate.set()
    wait_done(c, jid)
    assert (out / "_m_slow.mp4" / "x.torrent").exists()


def test_never_deletes_outside_the_output_folder(tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    outside = tmp_path / "precious"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    # a runner that returns the output root itself, and a damaged history line that points to ".."
    out_c = TestClient(create_app(manager=JobManager(out, tmp_path / "c.toml", lambda cfg, s, o, **k: o)))
    jid = bake(out_c, "/m/root.mp4")
    out_c.post(f"/jobs/{jid}/delete")
    assert out.exists() and (out / "history.jsonl").exists()
    (out / "history.jsonl").write_text('{"id": "evil", "source": "x", "status": "done", "out_dir": "..", "created": 1, "finished": 2}\n')
    m = JobManager(out, tmp_path / "c.toml", runner)
    assert m.delete("evil") == 1 and (outside / "keep.txt").exists() and tmp_path.exists() and out.exists()
