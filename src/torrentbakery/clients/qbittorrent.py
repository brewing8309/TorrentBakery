"""qBittorrent WebAPI v2 client: add a torrent stopped, let it recheck the existing files, then start seeding.
Same sequence as stash-empornium's client, but every step waits for qBittorrent to have done it: adding is asynchronous, qBittorrent 5
puts a torrent that was stopped before a recheck back to "Completed" (stopped) once the check is over, and a torrent whose files it does
not find at the save path must not be started (it would start downloading)."""
from __future__ import annotations

import time
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
            if c.get("app/version").status_code == 200:
                return c  # qBittorrent lets this address in without a login (Options > Web UI > "Bypass authentication for clients on...")
            r = c.post("auth/login", data={"username": self.cfg.username, "password": self.cfg.password})
        except httpx.HTTPError as e:
            c.close()
            raise QbitError(f"qBittorrent not reachable at {self.cfg.url}: {e}") from e
        if r.status_code == 403:
            c.close()
            raise QbitError("qBittorrent banned this address after failed logins")
        if r.status_code not in (200, 204) or (r.status_code == 200 and r.text.strip() != "Ok."):
            c.close()
            raise QbitError("qBittorrent login failed: wrong user name or password")
        return c

    def test(self) -> str:
        with closing(self._client()) as c:
            return "qBittorrent " + c.get("app/version").text.strip()

    # qBittorrent states (v4 says paused, v5 says stopped)
    CHECKING = ("checkingUP", "checkingDL", "checkingResumeData", "allocating", "moving")
    SEEDING = ("uploading", "stalledUP", "forcedUP", "queuedUP")

    poll_seconds = 1.0  # tests make it tiny
    check_timeout = 3600.0  # a big release takes a while to hash
    settle_timeout = 30.0

    def _info(self, c: httpx.Client, infohash: str) -> dict | None:
        r = c.get("torrents/info", params={"hashes": infohash})
        r.raise_for_status()
        return next((t for t in r.json() if t.get("hash", "").lower() == infohash.lower()), None)

    def _wait(self, c: httpx.Client, infohash: str, done, timeout: float) -> dict | None:
        """Poll the torrent until done(torrent) holds; returns the last state seen (None if qBittorrent never listed it)."""
        end, t = time.monotonic() + timeout, None
        while True:
            t = self._info(c, infohash) or t
            if t is not None and done(t) or time.monotonic() >= end:
                return t
            time.sleep(self.poll_seconds)

    def add(self, torrent: Path, infohash: str, save_path: str) -> str:
        """Add stopped into `save_path` (already mapped), recheck, and start seeding when configured. Returns what became of it."""
        with closing(self._client()) as c:
            fields = {"savepath": save_path, "paused": "true", "stopped": "true", "autoTMM": "false"}  # `stopped` is the qBittorrent 5 name
            if self.cfg.category:
                fields["category"] = self.cfg.category
            if self._info(c, infohash) is None:  # already there (e.g. stuck stopped from an earlier try): go on from where it is, qBittorrent refuses duplicates
                r = c.post("torrents/add", data=fields, files={"torrents": (torrent.name, torrent.read_bytes(), "application/x-bittorrent")})
                if r.status_code != 200 or r.text.strip().lower().startswith("fails"):
                    raise QbitError(f"qBittorrent refused the torrent (HTTP {r.status_code}: {r.text.strip()[:100]})")
            if self._wait(c, infohash, lambda t: True, self.settle_timeout) is None:
                raise QbitError("qBittorrent does not list the torrent after adding it")
            c.post("torrents/recheck", data={"hashes": infohash}).raise_for_status()
            seen = [False]
            began = time.monotonic()

            def checked(t: dict) -> bool:  # the state needs a moment to turn into "checking"; don't mistake the old one for a finished check
                seen[0] = seen[0] or t["state"] in self.CHECKING
                return t["state"] not in self.CHECKING and (seen[0] or time.monotonic() - began > 5)
            t = self._wait(c, infohash, checked, self.check_timeout)
            if t is None:
                raise QbitError("qBittorrent lost the torrent while checking it")
            if t["state"] in self.CHECKING:
                raise QbitError("qBittorrent is still checking the files (it keeps the torrent stopped); start it there when the check is done")
            if t.get("progress", 0) < 1:
                raise QbitError(f"qBittorrent finds only {t.get('progress', 0):.0%} of the files in {save_path}: the torrent was left stopped. "
                                "Check the path mapping in the qBittorrent settings")
            if not self.cfg.start:
                return "added (stopped, files complete)"
            for _ in range(4):  # start until it sticks: a start sent while qBittorrent was still busy can be undone
                r = c.post("torrents/start", data={"hashes": infohash})
                if r.status_code == 404:  # qBittorrent 4 calls it resume
                    r = c.post("torrents/resume", data={"hashes": infohash})
                r.raise_for_status()
                t = self._wait(c, infohash, lambda t: t["state"] in self.SEEDING or t["state"] in ("error", "missingFiles"), self.settle_timeout / 4)
                if t["state"] in self.SEEDING:
                    return f"seeding ({t['state']})"
                if t["state"] in ("error", "missingFiles"):
                    raise QbitError(f"qBittorrent reports {t['state']} for the torrent: check the path mapping and that the files are readable there")
            raise QbitError(f"qBittorrent did not start the torrent (state: {t['state']}); start it there")
