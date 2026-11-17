# TorrentBakery

Bakes a release package from a video file: `.torrent`, 5×7 screenshot sheet, main image, MediaInfo and a ready-to-paste BBCode forum post.
Planned: Stash / stash-box metadata, image-host upload (pixhost, hamsterimg, imagebam, imgbox), web UI, later TMDB/IMDb for regular movies.

## Status
Phase 1 (local file → package) works. Roadmap in the project plan: Phase 2 Stash + stash-box, Phase 3 image hosts, Phase 4 web UI.

## Usage
Needs `ffmpeg`, `ffprobe` and `mediainfo` on the PATH.

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/tb bake movie.mkv -o out [-t trackerprofile] [--cover image.jpg]
```

Output in `out/<name>/` (+ `out/<name>.zip`): `<name>.torrent`, `contactsheet.jpg`, `cover.jpg`, `mediainfo.txt`, `post.bbcode`, `release.json`.
Without an image host the post references the images by file name; upload them yourself and replace the names.

## Config (`~/.config/torrentbakery/config.toml`, optional)
```toml
template = "default.bbcode.j2"
[sheet]
cols = 5
rows = 7
thumb_width = 320
[tracker.main]
announce = "https://tracker.example/announce"
source = "ABC"
private = true
```
