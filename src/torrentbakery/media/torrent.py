from __future__ import annotations

from pathlib import Path

from torf import Torrent

from ..config import Tracker


def make_torrent(path: Path, dest: Path, tracker: Tracker | None = None, on_progress=None) -> Path:
    t = Torrent(path=path, created_by="TorrentBakery")
    if tracker:
        t.trackers = tracker.announce
        t.private = tracker.private
        if tracker.source:
            t.source = tracker.source
    # torf calls back (torrent, file, hashed pieces, total pieces) while hashing
    t.generate(callback=(lambda _t, _f, done, total: on_progress(done / total) or None) if on_progress else None, interval=0.5)
    t.write(dest, overwrite=True)
    return dest
