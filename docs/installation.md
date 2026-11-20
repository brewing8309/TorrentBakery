# 🧰 Installation & updates

[← Documentation](README.md)

TorrentBakery is installed from a release tarball, natively (no Docker). It runs well in a small unprivileged LXC container: 2 cores, 2 GB RAM and a few GB of disk are plenty; the work is done by `ffmpeg`.

## Requirements
- Debian 12/13 or Ubuntu 22.04+ (anything with `apt`, `systemd` and Python ≥ 3.11)
- Internet access to pypi.org during install (the venv is built there)
- Your videos mounted on the host, readable by the user `torrentbakery`
- `ffmpeg`, `mediainfo` and `python3-venv` are installed by `install.sh` if missing

## Install
```sh
curl -fLO https://github.com/brewing8309/TorrentBakery/releases/latest/download/torrentbakery-0.1.0.tar.gz
tar xzf torrentbakery-0.1.0.tar.gz
cd torrentbakery-0.1.0 && sudo ./install.sh
```
With the GitHub CLI: `gh release download v0.1.0 -R brewing8309/TorrentBakery`.

What `install.sh` sets up:

| | |
|---|---|
| `/opt/torrentbakery` | the program and its venv (replaced on every update) |
| `/var/lib/torrentbakery` | your data: `config.toml` (settings, keys, mode 0600), `out/` (baked releases, `history.jsonl`) |
| `torrentbakery.service` | systemd service, user `torrentbakery`, port 8080 on all interfaces |
| `/usr/local/bin/tb` | the command line, runs as the service user with the same settings |
| `/etc/default/torrentbakery` | optional overrides (`TB_HOST`, `TB_PORT`, `TB_OUT`) |

Another port: `sudo PORT=9000 ./install.sh`. Only local access: put `TB_HOST=127.0.0.1` into `/etc/default/torrentbakery` and restart the service.

## LXC tips (Proxmox)
- Mount your media share into the container with a bind mount (`mp0: /mnt/share,mp=/media/share,ro=1`). Read-only is enough for baking.
- Mount it **at the same path Stash uses**, then no path mapping is needed. Otherwise add one under *Settings → Stash → Path mapping*.
- In an unprivileged container the files must be readable by the mapped user (world-readable is the simplest).
- The output folder `/var/lib/torrentbakery/out` must be reachable by qBittorrent too if you want TorrentBakery to seed (see [Publishing](publishing.md#seeding-with-qbittorrent)).

## Another output folder
The service runs sandboxed and may only write to `/var/lib/torrentbakery`. To use another folder, set `TB_OUT=/srv/releases` in `/etc/default/torrentbakery` and allow it with a drop-in:
```sh
sudo systemctl edit torrentbakery
# [Service]
# ReadWritePaths=/srv/releases
sudo systemctl restart torrentbakery
```

## Update
Download and unpack the new version, run `sudo ./install.sh` again. Settings, history and releases stay. A running bake is lost (jobs live in memory), so update when the oven is empty.

## Remove
`sudo /opt/torrentbakery/uninstall.sh` removes program and service; `--purge` also deletes `/var/lib/torrentbakery` (settings and releases).

## Behind a reverse proxy (https)
Any proxy works (nginx, Caddy, Traefik, Nginx Proxy Manager). Forward to `http://<host>:8080`, nothing special needed. Turn on the [login](security.md) when it is reachable from outside your network. Example for Caddy:
```
bakery.example.org {
  reverse_proxy 10.0.0.16:8080
}
```

## Run from a checkout (development)
```sh
git clone https://github.com/brewing8309/TorrentBakery && cd TorrentBakery
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/tb serve --port 8080 --out out        # needs ffmpeg, ffprobe and mediainfo on the PATH
```
`scripts/build.sh` builds `dist/torrentbakery-<version>.tar.gz`. Pushing a tag `v<version>` (matching `__version__`) runs the tests and publishes a GitHub release with that tarball and the CHANGELOG section as notes.
