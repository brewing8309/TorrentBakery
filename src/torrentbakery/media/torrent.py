from __future__ import annotations

from pathlib import Path

from torf import Torrent

from ..config import Tracker


def make_torrent(path: Path, dest: Path, tracker: Tracker | None = None) -> Path:
    t = Torrent(path=path, created_by="TorrentBakery")
    if tracker:
        t.trackers = tracker.announce
        t.private = tracker.private
        if tracker.source:
            t.source = tracker.source
    t.generate()
    t.write(dest, overwrite=True)
    return dest
