# Changelog

## Unreleased

## 0.1.1 – 2026-11-21

### Fixed
- HappyFappy moved from `happyfappy.org` to `www.happyfappy.net`. The built-in template points to the new address. If you set up HappyFappy before, change the address under Settings → Trackers and paste fresh `cid` and `sid` cookies from the new domain.

### Changed
- Publishing shows live progress: a step list (prepare, hash, upload, cover and banner, qBittorrent), elapsed time, an animated bar for steps without a percentage, and a notice when the server stops answering, so a running upload no longer looks frozen.

### Security
- Cross-site requests are refused even without a login. Without a login, the UI only answers to IP addresses, localhost, single-label names and the names added under Settings → Login → Host names or in `TB_ALLOWED_HOSTS` (DNS rebinding).
- Saved secrets (qBittorrent password, tracker cookies and tokens, Stash API key) only go to the address they were saved for. Redirects to other sites drop them.
- Login throttling can no longer lock the owner out; IPv6 is counted per /64; `TB_TRUSTED_PROXIES` for the real client address behind a reverse proxy.
- A two-factor code can no longer be used twice by parallel requests.
- Output folder names can no longer point outside the output root. Stash scenes must lie inside the media folders too.
- Content-Security-Policy, no API browser (`/docs`), no server banner, hardened systemd unit, SHA256SUMS next to the release tarball.
- `SECURITY-AUDIT.md`: the audit review.

## 0.1.0 – 2026-11-20
The first release: from a video file or a Stash scene to a finished, seeding tracker upload.

### Baking
- One bake makes the `.torrent`, a 5×7 screenshot sheet, an animated cover, the MediaInfo and a BBCode forum post, plus a ZIP of everything.
- Sources: Stash scenes (searchable from the start page), single files, or whole folders (one torrent, the largest video provides the pictures).
- Animated cover (WebP/GIF) from 1.5-second clips spread over the video, or Stash's own preview; shrunk automatically below ~9 MB.
- Metadata from Stash and stash-box (StashDB): title, performers, studio, tags, description and cover; path mappings when Stash sees the files elsewhere.
- Image hosts: pixhost (.to/.cc/pixho.st), imgbox, imagebam, hamsterimg, imghost.dev and bitporn.eu; the images always stay in the release folder too.

### Publishing and seeding
- Gazelle trackers through their upload form with your session cookies, UNIT3D trackers through their API with a token.
- Built-in tracker templates for HappyFappy (Gazelle) and bitporn (UNIT3D); add more as small, data-only TOML files ([guide](https://github.com/brewing8309/TorrentBakery/blob/main/docs/tracker-templates.md)).
- Review step before every upload in the web UI: title, post, tags and category, all editable, categories read live from the tracker.
- UNIT3D list cover and banner set through the edit form (with an optional web session cookie).
- qBittorrent: rechecks, starts only with complete files and confirms the torrent seeds; nothing is ever downloaded by accident.
- Tags sent the way trackers write them (lowercase, dotted), performers first, housekeeping tags dropped, own never-send list.

### Web UI
- Bake, History and Settings pages in English, light and dark theme, phone layout with a bottom tab bar.
- Live progress with the screenshot sheet filling up, result page with downloads and BBCode preview, history that survives restarts.
- Version and GitHub link in the header.
- Optional login with TOTP two-factor authentication and recovery codes (scrypt, in-memory sessions, throttling, CSRF check).

### Install
- Release tarball with `install.sh` (systemd service, venv in `/opt/torrentbakery`, data in `/var/lib/torrentbakery`, `tb` command) and `uninstall.sh`; native, no Docker.
- Full CLI (`tb bake`, `tb publish`, `tb seed`, `tb tracker`, `tb stash`, …) sharing the service's settings.

### Documentation and license
- New README and documentation in `docs/`: installation, web UI, configuration, publishing, adding your own tracker, login & 2FA, CLI.
- Licensed under CC BY-NC 4.0: free to use, change and share with credit to this project; no commercial use.
