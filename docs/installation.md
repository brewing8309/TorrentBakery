# 🧰 Installation & updates

[← Documentation](README.md)

TorrentBakery is installed from a release tarball, natively, or runs as a [Docker image](#docker). The native way runs well in a small unprivileged LXC container: 2 cores, 2 GB RAM and a few GB of disk are plenty; the work is done by `ffmpeg`.

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
| `/etc/default/torrentbakery` | optional overrides (`TB_HOST`, `TB_PORT`, `TB_OUT`, `TB_ALLOWED_HOSTS`, `TB_TRUSTED_PROXIES`) |

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
Any proxy works (nginx, Caddy, Traefik, Nginx Proxy Manager). Forward to `http://<host>:8080` and pass the original `Host` header (Caddy and NPM do; nginx needs `proxy_set_header Host $host;`). Turn on the [login](security.md) when it is reachable from outside your network. In `/etc/default/torrentbakery`, set `TB_TRUSTED_PROXIES=<proxy address>` so failed logins are counted per real client, not per proxy. While the login is off, also add the proxy's host name under *Settings → Login → Host names* (or in `TB_ALLOWED_HOSTS`). Example for Caddy:
```
bakery.example.org {
  reverse_proxy 10.0.0.16:8080
}
```

## Docker
For Docker hosts (NAS, Unraid, VM). The image contains `ffmpeg`, `mediainfo` and the web UI, and runs as a non-root user. Copy [`docker-compose.example.yml`](../docker-compose.example.yml) and start it:
```sh
docker compose up -d        # then open http://<host>:8080/
```
- **Volumes:** `/data` holds `config.toml` (all secrets, mode 0600), `history.jsonl` and `out/` (releases). Mount your videos read-only at the **same path as in Stash**, so no path mapping is needed.
- **PUID / PGID:** the container starts as root only to hand `/data` to that user (default 1000:1000), then drops privileges. With `--user` / `user:` it starts unprivileged and `/data` must already be writable for that user.
- **Command line:** run `tb` as the service user, so `config.toml` stays readable: `docker exec -it -u torrentbakery torrentbakery tb auth set-password USER`, or one-off `docker run --rm -v ./data:/data -v /media:/media:ro ghcr.io/brewing8309/torrentbakery tb bake /media/demo.mp4`.
- **Update:** check `curl localhost:8080/busy` shows `"running": 0` (a restart kills running bakes, jobs live in memory), then `docker compose pull && docker compose up -d`.
- **Reverse proxy:** set `TB_TRUSTED_PROXIES` to the proxy's network and, while the login is off, `TB_ALLOWED_HOSTS` to its host name (see above).
- **qBittorrent:** `qbit.path_map` must map to the paths qBittorrent sees, not to the container's.
- **Package visibility:** while the repository is private, so is the image; `docker login ghcr.io` first.

## Run from a checkout (development)
```sh
git clone https://github.com/brewing8309/TorrentBakery && cd TorrentBakery
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/pytest -q
.venv/bin/tb serve --port 8080 --out out        # needs ffmpeg, ffprobe and mediainfo on the PATH
```
`scripts/build.sh` builds `dist/torrentbakery-<version>.tar.gz`. Pushing a tag `v<version>` (matching `__version__`) runs the tests and publishes a GitHub release with that tarball and the CHANGELOG section as notes.
