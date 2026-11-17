import zipfile

from PIL import Image
from torf import Torrent

from torrentbakery.config import Config, Tracker, load
from torrentbakery.media.contactsheet import timestamps
from torrentbakery.media.probe import probe
from torrentbakery.models import Release
from torrentbakery.pipeline import bake


def test_probe(video):
    info = probe(video)
    assert (info.width, info.height) == (640, 360)
    assert 59 < info.duration < 61
    assert info.video_codec == "h264" and info.audio[0].codec == "aac"
    assert "General" in info.full_text
    assert str(video.parent) not in info.full_text  # no host paths in the post


def test_timestamps_inside_runtime():
    ts = timestamps(100, 35)
    assert len(ts) == 35 and 5 < ts[0] and ts[-1] < 95 and ts == sorted(ts)


def test_bake(video, tmp_path):
    cfg = Config(trackers={"t": Tracker(announce=["https://tracker.example/announce"], source="TB")})
    release = Release(title="Test Scene", studio="Studio X", performers=["A", "B"], tags=["x", "y"], date="2026-01-02", description="Hello")
    out = bake(video, tmp_path, cfg, release=release, tracker="t")

    sheet = Image.open(out / "contactsheet.jpg")
    assert sheet.width == 5 * 320 + 6 * 8
    assert sheet.height > 7 * 180

    torrent = Torrent.read(next(out.glob("*.torrent")))
    assert torrent.private and torrent.source == "TB"
    assert list(torrent.trackers.flat) == ["https://tracker.example/announce"]

    post = (out / "post.bbcode").read_text()
    for needle in ("Test Scene", "Studio X", "A, B", "640x360", "[b]Video:[/b] H.264", "[b]Audio:[/b] AAC, 1 ch", "[b]Overall bit rate:[/b]", "[spoiler=MediaInfo]"):
        assert needle in post
    assert "{{" not in post and "[list]" not in post and "[*]" not in post  # the target forum does not render lists
    assert ", und" not in post  # undefined audio language is left out

    assert (tmp_path / f"{out.name}.zip").exists()
    names = zipfile.ZipFile(tmp_path / f"{out.name}.zip").namelist()
    assert {"post.bbcode", "cover.jpg", "contactsheet.jpg", "release.json"} <= set(names)


def test_config_load(tmp_path):
    p = tmp_path / "c.toml"
    p.write_text('[sheet]\ncols = 4\n[tracker.main]\nannounce = "https://a/announce"\nsource = "S"\n')
    cfg = load(p)
    assert cfg.sheet_cols == 4 and cfg.trackers["main"].announce == ["https://a/announce"]
