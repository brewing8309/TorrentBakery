# TorrentBakery

Bakes a release package from a video file: `.torrent`, 5×7 screenshot sheet, main image, MediaInfo and a ready-to-paste BBCode forum post.
Planned: Stash / stash-box metadata, image-host upload (pixhost, hamsterimg, imagebam, imgbox), web UI, later TMDB/IMDb for regular movies.

## Status
Phases 1–3 (local file → package, Stash/stash-box metadata, image-host upload) are implemented and covered by mocked tests. Verified live: the stash-box query against StashDB. Not yet verified live: Stash itself and the image-host uploads (pixhost is built from its OpenAPI spec; imgbox, imagebam and hamsterimg replay the hosts' web/API flows). Next: Phase 4 web UI.

## Usage
Needs `ffmpeg`, `ffprobe` and `mediainfo` on the PATH.

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/tb bake movie.mkv -o out [-t trackerprofile] [--cover image.jpg]
```

Output in `out/<name>/` (+ `out/<name>.zip`): `<name>.torrent`, `contactsheet.jpg`, `cover.jpg`, `mediainfo.txt`, `post.bbcode`, `release.json`.
Without an image host the post references the images by file name; upload them yourself and replace the names.

## Stash / stash-box
```
tb stash set http://stash:9999 --api-key KEY          # saved in the config
tb stash map-add /data/media /mnt/nas                  # Stash path prefix -> local mount (only if they differ)
tb bake stash:1234 -t main                             # scene 1234 from Stash
tb stash box-add stashdb https://stashdb.org/graphql --api-key KEY
tb bake file.mp4 --stashbox https://stashdb.org/scenes/<id>   # plain file + stash-box metadata
```
For `stash:<id>`, a stash-box link on the scene is used if Stash holds its key: title, performers, tags, description, links and the cover (largest landscape image) then come from the stash-box, otherwise from Stash (cover: Stash screenshot).

## Image hosts
```
tb bake file.mp4 --upload pixhost.cc          # also pixhost.to, pixho.st, imgbox, imagebam, hamsterimg
tb host set hamsterimg -o api_key=KEY         # hamsterimg (Chevereto) needs an API key; others need none
```
Uploads are marked adult by default (`--sfw` to change). The images are always also written to the output folder/ZIP, so you can upload them by hand instead.

## Tracker profiles
```
tb tracker add main -a https://tracker.example/announce -s ABC   # --public for non-private
tb tracker list | tb tracker remove main
```

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
