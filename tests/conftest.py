import subprocess

import pytest


@pytest.fixture(scope="session")
def video(tmp_path_factory):
    path = tmp_path_factory.mktemp("media") / "test clip.mp4"
    subprocess.run(
        ["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=duration=60:size=640x360:rate=24",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=60", "-c:v", "libx264", "-c:a", "aac", "-shortest", str(path)],
        check=True,
    )
    return path


@pytest.fixture(autouse=True)
def fast_qbit(monkeypatch):
    from torrentbakery.clients import QBittorrent
    monkeypatch.setattr(QBittorrent, "poll_seconds", 0.0)
    monkeypatch.setattr(QBittorrent, "settle_timeout", 0.2)  # tests that wait for something that never happens must not take 30 s
    monkeypatch.setattr(QBittorrent, "check_timeout", 2.0)


@pytest.fixture(autouse=True)
def test_host(monkeypatch):
    monkeypatch.setenv("TB_ALLOWED_HOSTS", "tb.test")  # the web tests talk to http://tb.test; without a login only allowed names answer
