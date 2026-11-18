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
tb bake file.mp4 --upload pixhost.cc          # also pixhost.to, pixho.st, imgbox, imagebam, hamsterimg, imghost.dev
tb host set imghost.dev -o api_key=KEY        # hamsterimg and imghost.dev (Chevereto) need an API key; others need none
```
Uploads are marked adult by default (`--sfw` to change). **bitporn:** UNIT3D shows images of hosts it has not whitelisted through the proxy wsrv.nl, which blocks adult hosts like pixhost (broken images in the post). Use `imghost.dev`, bitporn's own host; the review step warns when the bake used another one.
The images are always also written to the output folder/ZIP, so you can upload them by hand instead.

## Tracker profiles
```
tb tracker add main -a https://tracker.example/announce -s ABC   # --public for non-private
tb tracker list | tb tracker remove main
```

## Publishing to a tracker
A tracker profile with a site type (Gazelle: `empornium`, `happyfappy`, `femdomcult`, `enthralled`; UNIT3D: `bitporn`) and your session cookie (UNIT3D: API token) can upload a baked release for you. TorrentBakery does server-side what the [stash-empornium](https://github.com/bdbenim/stash-empornium) userscript does in the browser: it reads `upload.php`, fills the form (title, post, tags, cover, category, anonymity) and posts it with the torrent. Afterwards the torrent goes to qBittorrent, which rechecks the files and starts seeding.
```
tb tracker add emp -s Emp                         # announce URL is optional for publishing profiles
tb tracker set emp --site empornium --url https://www.empornium.sx --cookie 'session=…' --category Amateur   # name or id
tb tracker test emp                               # logs in, lists the category ids; posts nothing
tb tracker fetch-announce emp                     # reads your announce URL from the upload page
tb qbit set --url http://10.10.1.5:8080 --username u --password p --path-map /media/share=/data
tb publish out/My_Release -t emp --dry-run        # shows what would be sent
tb publish out/My_Release -t emp                  # uploads (public!) and adds to qBittorrent
tb bake stash:1234 -t emp -u pixhost.to --publish # bake and upload in one go (needs an image host: the tracker wants a cover URL)
```
In the web UI a bake never uploads by itself: the result page shows what was baked (title, post, tags, images) and ends with a **review step** (title, post, tags and category list read live from the tracker, all editable) before you press Publish. `--publish` / the profile's auto-publish setting uploads right after baking on the command line only. A failed upload never fails the bake; the reason is shown on the result page. The cookie expires at some point: "Test login" tells you, paste a fresh one.

Stash tags are translated to tracker tags with the default list from stash-empornium (Unlicense) plus your own entries (`tb tags map "Big Tits" "big.tits boobs" [-t emp]`, `tb tags ignore TAG`, or in the web UI). The performers are always added as tags (first); metadata such as date, resolution or codec is not tagged. Tags the list does not know are sent in dotted form, and the preview offers to remember them.

**UNIT3D (bitporn.eu)** has a real upload API, so there is no form scraping and no cookie: put your API token (account settings → Security) in the profile's cookie field, set the category id (`tb tracker test bitporn` lists them) and no announce URL is needed, the tracker adds your passkey itself. Category, type and resolution ids of the site are built in (`trackers/unit3d.py`); tags go into the keywords field with dots as spaces, the BBCode post as description. The torrent that is seeded is the one the tracker serves after the upload. The API cannot set the list cover and banner of a torrent. bitporn's edit form takes them as image URLs (hidden `cover_url` / `banner_url`), so with the browser cookie in the profile's "Web session cookie" field (`--session-cookie`, e.g. `bitporn_session=…`) TorrentBakery cuts a 400×600 poster and a 960×540 banner from the release's promo image (a video frame if there is none), uploads both to the bake's image host and enters the URLs through the edit form right after the upload (the post is read back from the page and sent along unchanged; it is never sent empty). Without that cookie the upload still works; "Set cover and banner again" on the result page retries. Another UNIT3D site needs one entry with its ids in `UNIT3D_SITES`.

The field names of the four Gazelle upload pages are looked up by the element ids the userscript uses; they are not verified against the live sites yet, so run `--dry-run` first.

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
