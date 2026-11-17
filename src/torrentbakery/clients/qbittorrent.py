"""qBittorrent WebAPI v2 client: add a torrent paused, let it recheck the existing files, then start seeding.
Same sequence as stash-empornium's client."""
from __future__ import annotations

from contextlib import closing
from pathlib import Path

import httpx

from ..config import QbitCfg


class QbitError(Exception):
    pass


def map_save_path(path: str, path_map: list[tuple[str, str]]) -> str:
    """Translate a directory as TorrentBakery sees it into the one qBittorrent sees (first matching prefix)."""
    for src, dst in path_map:
        s = src.rstrip("/")
        if path == s or path.startswith(s + "/"):
            return dst.rstrip("/") + path[len(s):]
    return path


class QBittorrent:
    def __init__(self, cfg: QbitCfg, transport: httpx.BaseTransport | None = None):
        if not cfg.url:
            raise QbitError("qBittorrent is not configured (tb qbit set --url …)")
        self.cfg, self.transport = cfg, transport

    def _client(self) -> httpx.Client:
        base = self.cfg.url.rstrip("/") + "/api/v2/"
        c = httpx.Client(base_url=base, transport=self.transport, timeout=30, headers={"Referer": self.cfg.url})
        try:
            r = c.post("auth/login", data={"username": self.cfg.username, "password": self.cfg.password})
        except httpx.HTTPError as e:
            c.close()
            raise QbitError(f"qBittorrent not reachable at {self.cfg.url}: {e}") from e
        if r.status_code == 403:
            c.close()
            raise QbitError("qBittorrent banned this address after failed logins")
        if r.status_code != 200 or r.text.strip() != "Ok.":
            c.close()
            raise QbitError("qBittorrent login failed: wrong user name or password")
        return c

    def test(self) -> str:
        with closing(self._client()) as c:
            return "qBittorrent " + c.get("app/version").text.strip()

    def add(self, torrent: Path, infohash: str, save_path: str) -> None:
        """Add paused into `save_path` (already mapped), recheck, and start when configured."""
        with closing(self._client()) as c:
            fields = {"savepath": save_path, "paused": "true", "stopped": "true", "autoTMM": "false"}  # `stopped` is the qBittorrent 5 name
            if self.cfg.category:
                fields["category"] = self.cfg.category
            r = c.post("torrents/add", data=fields, files={"torrents": (torrent.name, torrent.read_bytes(), "application/x-bittorrent")})
            if r.status_code != 200 or r.text.strip().lower().startswith("fails"):
                raise QbitError(f"qBittorrent refused the torrent (HTTP {r.status_code}: {r.text.strip()[:100]})")
            c.post("torrents/recheck", data={"hashes": infohash}).raise_for_status()
            if self.cfg.start:
                r = c.post("torrents/start", data={"hashes": infohash})
                if r.status_code == 404:  # qBittorrent 4 calls it resume
                    r = c.post("torrents/resume", data={"hashes": infohash})
                r.raise_for_status()
