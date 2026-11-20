# ⚙️ Configuration

[← Documentation](README.md)

Everything here can be set in the web UI under **Settings**, or with `tb` on the command line. Both write the same file: `/var/lib/torrentbakery/config.toml` for the installed service (from a checkout: `~/.config/torrentbakery/config.toml`, or `TB_CONFIG`). Changes apply to the next bake, no restart needed.

## Stash and stash-box
```sh
tb stash set http://stash:9999 --api-key KEY              # Stash under Settings → Security → API Key
tb stash box-add stashdb https://stashdb.org/graphql --api-key KEY
tb stash map-add /data/media /media/share                  # Stash path prefix → local mount (only if they differ)
```
- For a Stash scene (`stash:<id>` or the search on the start page) TorrentBakery reads title, studio, performers, tags, description and the file path from Stash.
- If the scene is linked to a stash-box whose key is saved, the metadata and the cover (largest landscape image) come from the stash-box instead; this is usually richer.
- A plain file can get stash-box metadata too: `--stashbox https://stashdb.org/scenes/<id>` (or the field on the Bake page).
- **Path mapping**: Stash says `/data/media/x.mp4`, but here the share is at `/media/share/x.mp4`? Map `/data/media` → `/media/share`. Not needed when the share is mounted at the same path.

## Animated cover
The cover is a WebP (or GIF) built from 1.5-second clips spread over 5–95 % of the runtime: about one clip per 2.5 minutes, 4–12 clips (30 min → 12 clips = 18 s). Above ~9 MB it is shrunk (width, frame rate, quality) until it fits.

| Option | Values |
|---|---|
| source | `own` (build it) or `stash` (use the animated preview Stash generated; falls back to own) |
| format | `webp` or `gif`. Hosts without WebP (imgbox, imagebam) get a GIF automatically |
| clips | `0` = automatic, or a fixed number |
| width | pixels, default 480 |

`--cover FILE` uses your own image, GIF or WebP instead.

## Screenshot sheet
5 columns × 7 rows, thumbnails 320 px wide, by default. Change it in `config.toml` (`[sheet]`, see below).

## Image hosts
```sh
tb bake file.mp4 --upload pixhost.cc
tb host set imghost.dev -o api_key=KEY
tb host list
```
| Host | Key needed | Notes |
|---|---|---|
| pixhost.to / pixhost.cc / pixho.st | – | WebP ok |
| imgbox | – | no WebP → GIF cover |
| imagebam | – | no WebP → GIF cover |
| hamsterimg | API key | untested in 0.1.0 |
| imghost.dev | API key | Chevereto; bitporn's own host |
| bitporn.eu | tracker session cookie | only with a bitporn profile |

Uploads are marked as adult content unless you tick *family safe* (`--sfw`). The images are always written to the release folder and the ZIP too, so you can upload them by hand.

> [!NOTE]
> Some trackers show foreign images through a proxy that blocks adult hosts (bitporn: wsrv.nl blocks pixhost). The tracker template restricts the hosts then, and the review step warns if your bake used another one.

## Tags
Stash tags are sent the way trackers write them: lowercase, dot-separated (`Anal Sex` → `anal.sex`). Performers always come first. Stash housekeeping tags ("4K Available", …) are dropped.
```sh
tb tags show "Anal Sex"      # what would be sent
tb tags ignore "Favorite"    # never send this tag
```

## The BBCode post
The post is rendered from a Jinja2 template (`default.bbcode.j2`, in `src/torrentbakery/render/templates/`). The release's data (title, performers, studio, date, description, tags, image URLs, file facts, MediaInfo) is available to it. To use your own, point `template` in `config.toml` to it.

## `config.toml`
You rarely need to edit it by hand; the keys mirror the settings pages.
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

[web]
media_roots = ["/media/share"]
```

## Environment variables
| Variable | Default (service) | Meaning |
|---|---|---|
| `TB_CONFIG` | `/var/lib/torrentbakery/config.toml` | settings file |
| `TB_OUT` | `/var/lib/torrentbakery/out` | where releases are written |
| `TB_HOST` | `0.0.0.0` | bind address of the web UI |
| `TB_PORT` | `8080` | port of the web UI |

Set them in `/etc/default/torrentbakery` and restart the service.
