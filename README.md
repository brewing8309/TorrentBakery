# TorrentBakery

Bakes a release package from a video file: `.torrent`, 5×7 screenshot sheet, main image, MediaInfo and a ready-to-paste BBCode forum post.
Planned: Stash / stash-box metadata, image-host upload (pixhost, hamsterimg, imagebam, imgbox), web UI, later TMDB/IMDb for regular movies.

## Status
Phases 1–3 (local file → package, Stash/stash-box metadata, image-host upload) are implemented and covered by mocked tests. Verified live: the stash-box query against StashDB. Verified live with a generated test image: pixhost.to/.cc/pixho.st, imgbox, imagebam. Not yet verified live: Stash itself and hamsterimg (needs an API key). Phase 4 web UI is in (`tb serve`); it has only been exercised with a local test video.

## Installation (Debian/Ubuntu host or LXC)
Release tarball, installed like Homelab Dashboard: `install.sh` sets up a venv in `/opt/torrentbakery`, a systemd service `torrentbakery` (user `torrentbakery`) and the `tb` command. It installs `ffmpeg`, `mediainfo` and `python3-venv` via apt if missing and needs Python ≥ 3.11 and access to pypi.org.
```
gh release download v0.1.0 -R brewing8309/TorrentBakery     # the repo is private, so plain curl needs a token
tar xzf torrentbakery-0.1.0.tar.gz
cd torrentbakery-0.1.0 && ./install.sh                       # another port: PORT=9000 ./install.sh
```
Then open `http://<host>:8080/` and set up trackers, Stash and media roots under **Settings**. The UI has no login; it listens on all interfaces by default (`TB_HOST=127.0.0.1` in `/etc/default/torrentbakery` restricts it).
- **Mounted shares** must be readable by the user `torrentbakery`; add them as media roots (or let Stash scenes map via path mappings).
- **Data:** `/var/lib/torrentbakery` (`config.toml` with the API keys, `out/` with the baked releases). Another output folder needs a systemd drop-in with `ReadWritePaths=`.
- **CLI:** `tb …` runs as the service user with the same settings, e.g. `tb bake stash:1234 -t main`.
- **Update:** unpack the new version, `./install.sh`; settings and output stay. **Remove:** `/opt/torrentbakery/uninstall.sh` (`--purge` also deletes settings and releases).
- **Build the tarball yourself:** `scripts/build.sh` → `dist/torrentbakery-<version>.tar.gz`; `./deploy.sh <vmid>` pushes it into a Proxmox container. A tag `v<version>` triggers the release workflow.

## Usage (from a checkout)
Needs `ffmpeg`, `ffprobe` and `mediainfo` on the PATH.

```
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/tb bake movie.mkv -o out [-t trackerprofile] [--cover image.jpg]
```

Output in `out/<name>/` (+ `out/<name>.zip`): `<name>.torrent`, `contactsheet.jpg`, `cover.webp` (or `.gif`), `mediainfo.txt`, `post.bbcode`, `release.json`.
Without an image host the post references the images by file name; upload them yourself and replace the names.

**Animated cover:** the cover is a WebP (or GIF) built from 1.5-second clips spread evenly over the video, 5–95 % of the runtime (about one clip per 2.5 minutes, 4–12 clips; 30 min → 12 clips = 18 s). `--cover-source stash` uses the animated preview Stash generated itself (WebP, Stash scenes only, falls back to a self-built cover); `--cover-format gif` forces GIF, and hosts without WebP support (imgbox, imagebam) get a GIF automatically. If the file exceeds ~9 MB it is shrunk (width, frame rate, quality) until it fits. `--cover FILE` uses your own image/GIF/WebP instead.

## Stash / stash-box
```
tb stash set http://stash:9999 --api-key KEY          # saved in the config
tb stash map-add /data/media /mnt/nas                  # Stash path prefix -> local mount (only if they differ)
tb bake stash:1234 -t main                             # scene 1234 from Stash
tb stash box-add stashdb https://stashdb.org/graphql --api-key KEY
tb bake file.mp4 --stashbox https://stashdb.org/scenes/<id>   # plain file + stash-box metadata
```
For `stash:<id>`, a stash-box link on the scene is used if Stash holds its key: title, performers, tags, description, links and the cover (largest landscape image) then come from the stash-box, otherwise from Stash (cover: Stash screenshot).

## Web UI
```
.venv/bin/pip install -e '.[web]'
.venv/bin/tb serve --host 0.0.0.0 --port 8080 --out /srv/torrentbakery/out
```
The interface is in English. Pages: **Bake** (search Stash, enter a scene ID or pick a file/folder with the **Browse…** pop-up, pick tracker profile and image host, live progress, post preview with copy button, downloads for the ZIP, torrent and each image) **History** (every bake, newest first, kept across restarts in `<out>/history.jsonl`, each entry reopens its result page) and **Settings** (tracker profiles, animated-cover defaults, Stash URL/key and path mappings, stash-box keys, image-host options, media roots). The UI has no login: keep it on a trusted network. API keys are never shown again after saving. Plain files and folders can only be baked from (and browsed inside) the folders listed as media roots; Stash scenes need none. A folder becomes one torrent of the whole folder; its largest video provides the screenshots and the cover.

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
[cover]
source = "own"   # own | stash
format = "webp"  # webp | gif
clips = 0        # 0 = automatic
width = 480
[tracker.main]
announce = "https://tracker.example/announce"
source = "ABC"
private = true
```
