<img src="../src/torrentbakery/web/static/brand/mark.svg" alt="" width="56" align="right">

# TorrentBakery documentation

New here? Start with the [Quick start](../README.md#-quick-start) in the main README.

| Page | What's in it |
|---|---|
| 🧰 [Installation & updates](installation.md) | Tarball install, LXC tips, update, remove, reverse proxy, running from a checkout |
| 🖥️ [Using the web UI](web-ui.md) | Bake, result page, review step, history, settings |
| ⚙️ [Configuration](configuration.md) | Stash & stash-box, path mappings, covers, sheet, image hosts, qBittorrent, tags, `config.toml` |
| 📤 [Publishing to trackers](publishing.md) | Gazelle and UNIT3D, cookies and tokens, review step, seeding |
| 🧩 [Add your own tracker](tracker-templates.md) | Step-by-step guide and full reference for tracker templates |
| 🔐 [Login & 2FA](security.md) | Optional login, TOTP, recovery codes, what is protected and what not |
| ⌨️ [Command line](cli.md) | Every `tb` command with examples |

## Words used in these pages
- **Bake**: one run that turns a source (Stash scene, file or folder) into a release folder.
- **Release folder**: `out/<name>/` with `<name>.torrent`, `contactsheet.jpg`, `cover.webp` (or `.gif`), `mediainfo.txt`, `post.bbcode`, `release.json`, plus `out/<name>.zip`.
- **Tracker template**: a TOML file describing a tracker *site* (engine, login, form). Shipped or community-made.
- **Tracker profile**: *your* settings for one tracker: template + announce URL, source flag, category, cookie/token.
