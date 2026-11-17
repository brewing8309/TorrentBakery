import pytest
from PIL import Image

from torrentbakery.media.animcover import CLIP_SECONDS, auto_clip_count, clip_starts, make_animated_cover


def test_auto_clip_count():
    assert [auto_clip_count(d) for d in (20, 300, 1793, 7200)] == [4, 4, 11, 12]


def test_clip_starts_spread_and_inside_video():
    starts = clip_starts(1800, 12)
    assert len(starts) == 12 and starts == sorted(starts)
    assert starts[0] > 1800 * 0.04 and starts[-1] + CLIP_SECONDS < 1800 * 0.96  # skips intro and credits
    gaps = [b - a for a, b in zip(starts, starts[1:])]
    assert max(gaps) - min(gaps) < 1e-6  # evenly spread
    assert clip_starts(2.0, 5) == [0.0]  # too short for several clips
    assert clip_starts(10, 4)[-1] + CLIP_SECONDS <= 10


@pytest.mark.parametrize("fmt", ["webp", "gif"])
def test_animated_cover_is_animated_and_clips_are_1_5_seconds(video, tmp_path, fmt):
    out = make_animated_cover(video, 60.0, tmp_path, fmt, width=320)
    assert out.name == f"cover.{fmt}"
    img = Image.open(out)
    clips = 4  # a 60 s video gets the minimum of 4 clips
    assert getattr(img, "is_animated", False) and img.width == 320
    total_ms = 0
    for i in range(img.n_frames):
        img.seek(i)
        img.load()  # WebP reports the frame duration only once the frame is loaded
        total_ms += img.info.get("duration", 0)
    assert abs(total_ms / 1000 - clips * CLIP_SECONDS) < 0.5, total_ms


def test_size_budget_shrinks_until_it_fits(video, tmp_path):
    big = make_animated_cover(video, 60.0, tmp_path, "gif", max_bytes=10**9).stat().st_size
    small = make_animated_cover(video, 60.0, tmp_path, "gif", max_bytes=int(big * 0.6)).stat().st_size
    assert small <= big * 0.6
    with pytest.raises(RuntimeError):
        make_animated_cover(video, 60.0, tmp_path, "gif", max_bytes=500)
