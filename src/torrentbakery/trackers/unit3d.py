"""Publisher for UNIT3D trackers (bitporn.eu). UNIT3D has a real upload API, so no cookie and no form scraping:
POST /api/torrents/upload with the account's API token (the profile's "cookie" field holds the token).
The tracker rewrites the uploaded torrent (own announce with your passkey, private flag, source), so the file to
seed is the one `download_torrent` fetches afterwards."""
from __future__ import annotations

import html as htmllib
import json
import re
import time
from urllib.parse import urljoin, urlparse

import httpx

from ..config import Tracker
from .base import LoginError, PublishRequest, PublishResult, TrackerError, UploadForm
from .gazelle import _error_text as _page_error
from .gazelle import _FormParser
from .template import get_template

UA = "Mozilla/5.0 (X11; Linux x86_64) TorrentBakery"

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
        self.site = get_template(tracker.site).unit3d

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
        note = ""
        if self.tracker.session_cookie:
            try:
                self._web_get(f"")
                note = "Web session for cover and banner works."
                if "remember_web_" not in self.tracker.session_cookie:
                    note += " But it has no remember_web_… cookie, so it will expire within hours: log in again with \"Remember me\" ticked and paste both cookies."
            except LoginError:
                note = "The web session cookie is expired or wrong: cover and banner cannot be set until you paste a fresh one."
            except (TrackerError, httpx.HTTPError):
                note = "The web session could not be checked."
        else:
            note = "No web session cookie saved: uploads work, but cover and banner for the list stay empty."
        return UploadForm(categories=[(str(i), n) for i, n in sorted(self.site["categories"].items(), key=lambda x: x[1].lower())], note=note)

    # ---- what the API cannot do: the list cover and banner, only the web form takes them ----
    def _web(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=120, follow_redirects=False, headers={"Cookie": self.tracker.session_cookie, "User-Agent": UA})

    def _web_get(self, path: str) -> tuple[httpx.Response, str]:
        """GET a page of the site with the browser session; a bounce to the login page means the session is dead."""
        url = self._url(path)
        with self._web() as c:
            for _ in range(5):
                r = c.get(url)
                if not r.is_redirect:
                    break
                url = urljoin(url, r.headers["location"])
                if "login" in urlparse(url).path:
                    raise LoginError(f"{self.name}: the web session cookie is expired or wrong (redirected to the login page)")
        if r.status_code in (401, 403, 419):
            raise LoginError(f"{self.name}: the web session was refused (HTTP {r.status_code})")
        if r.status_code != 200:
            raise TrackerError(f"{self.name}: {urlparse(url).path or '/'} answered HTTP {r.status_code}")
        return r, url

    def set_artwork(self, torrent_id: str, cover_url: str, banner_url: str) -> None:
        """Opens the torrent's edit form with the web session, keeps everything it holds and sets its two list images. bitporn's form takes
        them as URLs (hidden cover_url / banner_url, which its "Set as Cover" buttons fill); the owner may edit for a day after uploading."""
        if not self.tracker.session_cookie:
            raise TrackerError(f"{self.name}: no web session cookie saved (Settings → tracker profile → Web session cookie)")
        page, url = self._web_get(f"torrents/{torrent_id}/edit")
        p = _FormParser()
        p.feed(page.text)
        form = next((f for f in p.forms if any(c["name"] == "cover_url" for c in f["controls"])), None)
        if form is None:
            raise TrackerError(f"{self.name}: the edit page has no cover field (not your torrent, or the one-day edit window is over)")
        data = form_values(form)
        for name, text in livewire_texts(page.text).items():  # the BBCode editors are filled in the browser; the page carries their text in a snapshot
            if not data.get(name):
                data[name] = text
        if not data.get("description"):
            raise TrackerError(f"{self.name}: could not read the post from the edit page; not sending (it would erase the description)")
        data["cover_url"], data["banner_url"] = cover_url, banner_url
        action = urljoin(url, form["action"] or f"/torrents/{torrent_id}")
        with self._web() as c:
            r = c.post(action, data=data)
            if r.is_redirect:
                loc = urljoin(action, r.headers["location"])
                if "login" in urlparse(loc).path:
                    raise LoginError(f"{self.name}: the web session ended; paste a fresh cookie")
                if urlparse(loc).path.rstrip("/").endswith("/edit"):  # sent back to the form: validation failed
                    back = c.get(loc)
                    raise TrackerError(f"{self.name}: cover and banner rejected: {_page_error(back.text) or 'the form was sent back without a message'}")
                return
        raise TrackerError(f"{self.name}: cover and banner rejected: {_page_error(r.text) or f'HTTP {r.status_code}'}")

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


def livewire_texts(html: str) -> dict[str, str]:
    """name -> BBCode of the Livewire editors on the page (their textareas are empty in the HTML)."""
    out: dict[str, str] = {}
    for m in re.finditer(r'wire:snapshot="([^"]*)"', html):
        try:
            d = json.loads(htmllib.unescape(m.group(1))).get("data", {})
        except ValueError:
            continue
        if isinstance(d, dict) and d.get("name") and isinstance(d.get("contentBbcode"), str):
            out[d["name"]] = d["contentBbcode"]
    return out


def form_values(form: dict) -> dict[str, str]:
    """What a browser would submit for the form as it stands (hidden _token and _method included); later fields win, so a checkbox overrides its hidden "0"."""
    data: dict[str, str] = {}
    for ctl in form["controls"]:
        name, kind, typ = ctl.get("name"), ctl["kind"], ctl.get("type")
        if not name:
            continue
        if kind == "input" and typ in ("hidden", "text", "number", "email", "url", "search"):
            data[name] = ctl["value"]
        elif kind == "input" and typ in ("checkbox", "radio"):
            if ctl["checked"]:
                data[name] = ctl["value"] or "on"
        elif kind == "textarea":
            data[name] = ctl["value"][1:] if ctl["value"].startswith("\n") else ctl["value"]
        elif kind == "select":
            sel = next((o for o in ctl["options"] if o["selected"]), ctl["options"][0] if ctl["options"] else None)
            data[name] = sel["value"] if sel else ""
    return data
