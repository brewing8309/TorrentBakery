"""Publisher for UNIT3D trackers (bitporn.eu). UNIT3D has a real upload API, so no cookie and no form scraping:
POST /api/torrents/upload with the account's API token (the profile's "cookie" field holds the token).
The tracker rewrites the uploaded torrent (own announce with your passkey, private flag, source), so the file to
seed is the one `download_torrent` fetches afterwards."""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin

import httpx

from ..config import Tracker
from .base import LoginError, PublishRequest, PublishResult, TrackerError, UploadForm

UA = "Mozilla/5.0 (X11; Linux x86_64) TorrentBakery"

# ids are specific to each installation and the API has no lookup endpoint, so they are listed here per site
UNIT3D_SITES: dict[str, dict] = {
    "bitporn": {
        "type_id": 1,  # "None"; 4 is WEB-DL
        "categories": {
            4: "Amateur", 5: "Anal", 6: "Asian", 7: "BBW", 8: "BDSM", 9: "Big Ass", 10: "Big Tits", 11: "Black", 12: "Cartoon", 13: "Casting",
            14: "Classic", 15: "Collection", 16: "Creampie", 17: "Oral / Cumshot", 18: "Deepthroat", 19: "Extreme", 20: "Fansite", 21: "Family",
            22: "Feature", 23: "Fetish", 24: "Fisting", 25: "Gangbang/Group sex", 26: "Game", 27: "Gay / Bi", 28: "Hair", 29: "Hardcore",
            30: "HiddenCam", 31: "Homemade", 32: "Interracial", 33: "Lesbian", 34: "Magyar", 35: "Masturbation", 36: "Mature", 37: "Milf",
            38: "Old and Young", 39: "Parody", 40: "Pictures", 41: "Pissing", 42: "POV", 43: "Pregnant", 44: "Public", 45: "Shemale", 46: "Softcore",
            47: "Squirt", 48: "Scat", 49: "Teen", 50: "Threesome", 51: "VR", 52: "Uncategorized", 53: "Movie", 54: "Ai Generated", 55: "PMV",
        },
        "resolutions": {
            11: "Other", 12: "SD", 13: "1080p(FHD)", 14: "2048p (2K)", 15: "3160p (6K)", 16: "4320p (8K)", 17: "720p(WXGA)", 18: "2160p (4K)",
            19: "1600p", 20: "1440p(QHD)", 21: "3072p (VR)",
        },
    },
}


def resolution_id(table: dict[int, str], height: int) -> int:
    """The id of the largest listed resolution (names like "1080p(FHD)") that fits `height`; SD or Other below/without a match."""
    best, best_p = None, 0
    for rid, name in table.items():
        m = re.match(r"(\d{3,4})p", name)
        if m and best_p < int(m.group(1)) <= height * 1.02:
            best, best_p = rid, int(m.group(1))
    if best is not None:
        return best
    for want in ("SD", "Other"):
        for rid, name in table.items():
            if name == want:
                return rid
    return next(iter(table))


class Unit3dPublisher:
    retry_delay = 2.0

    def __init__(self, name: str, tracker: Tracker, transport: httpx.BaseTransport | None = None):
        if not tracker.base_url or not tracker.cookie:
            raise ValueError(f"tracker {name!r} needs a base URL and an API token to publish (tb tracker set {name} --url … --cookie <token>)")
        self.name, self.tracker, self.transport = name, tracker, transport
        self.site = UNIT3D_SITES[tracker.site]

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=120, follow_redirects=True,
                            headers={"Authorization": f"Bearer {self.tracker.cookie}", "Accept": "application/json", "User-Agent": UA})

    def _url(self, path: str) -> str:
        return urljoin(self.tracker.base_url.rstrip("/") + "/", path)

    def _check(self, r: httpx.Response) -> None:
        if r.status_code in (401, 403):
            raise LoginError(f"{self.name}: the API token was refused (HTTP {r.status_code}); create a new one in your account settings")
        if r.status_code == 429:
            raise TrackerError(f"{self.name}: rate limit reached, try again in a minute")

    def fetch_form(self) -> UploadForm:
        """Checks the token with a harmless read and returns the category list."""
        with self._client() as c:
            r = c.get(self._url("api/torrents"), params={"perPage": 1})
        self._check(r)
        if r.status_code != 200:
            raise TrackerError(f"{self.name}: api/torrents answered HTTP {r.status_code}")
        return UploadForm(categories=[(str(i), n) for i, n in sorted(self.site["categories"].items(), key=lambda x: x[1].lower())])

    def _data(self, req: PublishRequest) -> dict[str, str]:
        cat = req.category or self.tracker.category
        if not cat:
            raise TrackerError(f"{self.name}: pick a category (tb tracker set {self.name} --category <id>)")
        if int(cat) not in self.site["categories"]:
            raise TrackerError(f"{self.name}: category {cat!r} is not in the known list")
        anon = "1" if (req.anonymous or self.tracker.anonymous) else "0"
        data = {
            "name": req.title, "description": req.description, "mediainfo": req.mediainfo, "bdinfo": "",
            "category_id": cat, "type_id": str(self.site["type_id"]),
            "resolution_id": str(resolution_id(self.site["resolutions"], req.resolution)),
            "keywords": ", ".join(t.replace(".", " ") for t in req.tags),
            "anon": anon, "anonymous": anon,  # the field was renamed between UNIT3D versions
            "tmdb": "0", "imdb": "0", "tvdb": "0", "mal": "0", "igdb": "0",  # required numbers; adult content has no such ids
            "personal_release": "0", "internal": "0", "refundable": "0", "stream": "0", "sd": "0", "free": "0", "doubleup": "0", "featured": "0", "sticky": "0",
        }
        return data

    def publish(self, req: PublishRequest, dry_run: bool = False) -> PublishResult:
        data = self._data(req)
        if dry_run:
            return PublishResult(dry_run=True, sent={**data, "torrent": req.torrent.name})
        with self._client() as c:
            r = c.post(self._url("api/torrents/upload"), data=data, files={"torrent": (req.torrent.name, req.torrent.read_bytes(), "application/x-bittorrent")})
        self._check(r)
        try:
            j = r.json()
        except ValueError:
            j = {}
        if not (isinstance(j, dict) and j.get("success") is True):
            raise TrackerError(f"{self.name}: upload rejected: {_error_text(j) or f'HTTP {r.status_code}, no error text'}")
        m = re.search(r"/download/(\d+)", str(j.get("data", "")))
        if not m:
            raise TrackerError(f"{self.name}: the upload was accepted but the answer holds no torrent id ({str(j.get('data'))[:100]}); look it up on the site")
        return PublishResult(torrent_url=self._url(f"torrents/{m.group(1)}"), torrent_id=m.group(1), sent=data)

    def download_torrent(self, torrent_id: str) -> bytes:
        """The .torrent as the tracker serves it (with your passkey)."""
        last = ""
        for attempt in range(3):
            if attempt:
                time.sleep(self.retry_delay)
            try:
                with self._client() as c:
                    meta = c.get(self._url(f"api/torrents/{torrent_id}"))
                    self._check(meta)
                    link = (meta.json().get("attributes") or {}).get("download_link", "") if meta.status_code == 200 else ""
                    if not link:
                        last = f"{self.name}: no download link for torrent {torrent_id} (HTTP {meta.status_code})"
                        continue
                    r = c.get(link)
                if r.status_code == 200 and r.content[:1] == b"d" and b"4:info" in r.content:
                    return r.content
                last = f"{self.name}: the download link did not return a torrent file (HTTP {r.status_code})"
            except LoginError:
                raise
            except (httpx.HTTPError, ValueError, AttributeError) as e:
                last = f"{self.name}: {type(e).__name__} while fetching the torrent"
        raise TrackerError(last)


def _error_text(j) -> str:
    if not isinstance(j, dict):
        return ""
    detail = j.get("data")
    if isinstance(detail, dict):  # field -> [messages]
        detail = "; ".join(f"{k}: {' '.join(v) if isinstance(v, list) else v}" for k, v in detail.items())
    return " ".join(f"{j.get('message', '')} {detail or ''}".split())[:300]
