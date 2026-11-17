"""Publisher for Gazelle-style trackers (Empornium, HappyFappy, FemdomCult, Enthralled).

It does what stash-empornium's userscript does in the browser, from the server: read upload.php with the session
cookie, fill the form `upload_table` (title, description, tags, cover, category, anonymity) and post it with the
torrent as `file_input`. Field names are looked up by the element ids the userscript uses, so a site that renames
a `name` attribute still works."""
from __future__ import annotations

import html as htmllib
import re
import time
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

import httpx

from ..config import Tracker
from .base import LoginError, PublishRequest, PublishResult, TrackerError, UploadForm

UA = "Mozilla/5.0 (X11; Linux x86_64) TorrentBakery"

# element id of each form field per site (stash-empornium: #title #desc #image, tags in #taginput, FemdomCult #tags)
FIELD_IDS = {"title": "title", "desc": "desc", "image": "image", "tags": "taginput"}
SITE_FIELD_IDS = {"femdomcult": {"tags": "tags"}}
CATEGORY_IDS = ("categories", "category", "type")


class _FormParser(HTMLParser):
    """Collects the controls of the upload form (or, failing that, the form that holds the file input)."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.forms: list[dict] = []
        self._form: dict | None = None
        self._select: dict | None = None
        self._option: dict | None = None
        self._textarea: dict | None = None
        self.announces: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if tag == "input" and "/announce" in a.get("value", ""):
            self.announces.append(a["value"])
        if tag == "form":
            self._form = {"id": a.get("id", ""), "action": a.get("action", ""), "controls": []}
            self.forms.append(self._form)
        elif self._form is None:
            return
        elif tag == "input":
            self._form["controls"].append({"kind": "input", "type": a.get("type", "text").lower(), "name": a.get("name", ""), "id": a.get("id", ""),
                                           "value": a.get("value", ""), "checked": "checked" in a})
        elif tag == "select":
            self._select = {"kind": "select", "name": a.get("name", ""), "id": a.get("id", ""), "options": [], "value": ""}
            self._form["controls"].append(self._select)
        elif tag == "option" and self._select is not None:
            self._option = {"value": a.get("value", ""), "label": "", "selected": "selected" in a}
            self._select["options"].append(self._option)
        elif tag == "textarea":
            self._textarea = {"kind": "textarea", "name": a.get("name", ""), "id": a.get("id", ""), "value": ""}
            self._form["controls"].append(self._textarea)

    def handle_endtag(self, tag):
        if tag == "form":
            self._form = None
        elif tag == "select":
            self._select = None
        elif tag == "option":
            self._option = None
        elif tag == "textarea":
            self._textarea = None

    def handle_data(self, data):
        if self._option is not None:
            self._option["label"] += data
        elif self._textarea is not None:
            self._textarea["value"] += data


def _upload_form(forms: list[dict]) -> dict | None:
    for f in forms:
        if f["id"] == "upload_table":
            return f
    return next((f for f in forms if any(c.get("type") == "file" for c in f["controls"])), None)


class GazellePublisher:
    def __init__(self, name: str, tracker: Tracker, transport: httpx.BaseTransport | None = None):
        if not tracker.base_url or not tracker.cookie:
            raise ValueError(f"tracker {name!r} needs a base URL and a session cookie to publish (tb tracker set {name} --url … --cookie …)")
        self.name, self.tracker, self.transport = name, tracker, transport
        self.ids = {**FIELD_IDS, **SITE_FIELD_IDS.get(tracker.site, {})}
        self._form: dict | None = None

    def _client(self) -> httpx.Client:
        return httpx.Client(transport=self.transport, timeout=60, headers={"Cookie": self.tracker.cookie, "User-Agent": UA}, follow_redirects=False)

    def _url(self, path: str) -> str:
        return urljoin(self.tracker.base_url.rstrip("/") + "/", path)

    def _follow(self, c: httpx.Client, url: str) -> tuple[httpx.Response, str]:
        """GET following redirects by hand: a bounce to the login page means the cookie is dead (sites also move between domains)."""
        for _ in range(5):
            r = c.get(url)
            if r.is_redirect:
                url = urljoin(url, r.headers["location"])
                if "login" in urlparse(url).path:
                    raise LoginError(f"{self.name}: the session cookie is expired or wrong (redirected to the login page)")
                continue
            break
        if r.status_code in (401, 403):
            raise LoginError(f"{self.name}: access denied (HTTP {r.status_code}); check the cookie")
        if r.status_code != 200:
            raise TrackerError(f"{self.name}: {urlparse(url).path.lstrip('/')} answered HTTP {r.status_code}")
        return r, url

    def _get_page(self, c: httpx.Client) -> tuple[_FormParser, str]:
        r, url = self._follow(c, self._url("upload.php"))
        p = _FormParser()
        p.feed(r.text)
        if _upload_form(p.forms) is None:
            if "login" in r.text.lower() and "password" in r.text.lower():
                raise LoginError(f"{self.name}: the session cookie is expired or wrong (login page)")
            raise TrackerError(f"{self.name}: no upload form on upload.php (account may lack upload rights)")
        return p, url

    def fetch_form(self) -> UploadForm:
        with self._client() as c:
            p, url = self._get_page(c)
        form = _upload_form(p.forms)
        self._form = form
        fields = {}
        for ctl in form["controls"]:
            if ctl["kind"] == "input" and ctl["type"] == "hidden" and ctl["name"]:
                fields[ctl["name"]] = ctl["value"]
        cats = []
        for ctl in form["controls"]:
            if ctl["kind"] == "select" and (ctl["id"] in CATEGORY_IDS or ctl["name"] in CATEGORY_IDS):
                cats = [(o["value"], o["label"].strip()) for o in ctl["options"] if o["value"] not in ("", "0")]
                break
        return UploadForm(action=urljoin(url, form["action"] or "upload.php"), fields=fields, categories=cats, announce=p.announces[0] if p.announces else "")

    retry_delay = 2.0  # the tracker may need a moment before a fresh upload can be downloaded

    def download_torrent(self, torrent_id: str) -> bytes:
        """The .torrent exactly as the tracker serves it to you. Trackers rewrite uploaded torrents (own source flag, cleaned info
        dictionary), which changes the infohash, so only this file is the one that works with the tracker when seeding.
        The download link is read from the torrent's page; if the page has none, it is built from the form's auth token and your passkey."""
        last = ""
        for attempt in range(3):
            if attempt:
                time.sleep(self.retry_delay)
            try:
                with self._client() as c:
                    page, url = self._follow(c, self._url(f"torrents.php?id={torrent_id}"))
                    links = [htmllib.unescape(m) for m in re.findall(r"""href=["']([^"']*action=download[^"']*)""", page.text)]
                    link = next((link for link in links if re.search(rf"[?&]id={torrent_id}\b", link)), None)
                    if link is None:
                        form_page, _ = self._get_page(c)
                        auth = next((x["value"] for x in _upload_form(form_page.forms)["controls"] if x.get("name") == "auth"), "")
                        m = re.search(r"/([^/]+)/announce", form_page.announces[0] if form_page.announces else "")
                        if not (auth and m):
                            raise TrackerError(f"{self.name}: no download link on the torrent page and no auth token/passkey to build one")
                        link = f"torrents.php?action=download&id={torrent_id}&authkey={auth}&torrent_pass={m.group(1)}"
                    r = c.get(urljoin(url, link))
                    if r.is_redirect:
                        r, _ = self._follow(c, urljoin(url, r.headers["location"]))
                if r.status_code == 200 and r.content[:1] == b"d" and b"4:info" in r.content:
                    return r.content
                last = f"{self.name}: the download link did not return a torrent file (HTTP {r.status_code})"
            except LoginError:
                raise
            except (TrackerError, httpx.HTTPError) as e:
                last = str(e)
        raise TrackerError(last)

    def _by_id(self, form: dict, key: str) -> dict | None:
        want = self.ids[key]
        return next((c for c in form["controls"] if c.get("id") == want and c.get("name")), None)

    def _build(self, form: dict, req: PublishRequest) -> dict[str, str | list[str]]:
        data: dict[str, str | list[str]] = {}
        radios: dict[str, list[dict]] = {}
        upload_button = self._upload_button(form)
        for ctl in form["controls"]:
            if not ctl.get("name"):
                continue
            kind, typ = ctl["kind"], ctl.get("type")
            if kind == "input" and typ in ("hidden", "text", "number"):
                data[ctl["name"]] = ctl["value"]
            elif kind == "input" and typ == "checkbox" and ctl["checked"]:
                data.setdefault(ctl["name"], []).append(ctl["value"] or "on")  # type: ignore[union-attr]
            elif kind == "input" and typ == "radio":
                radios.setdefault(ctl["name"], []).append(ctl)
            elif kind == "input" and typ == "submit" and ctl is upload_button:
                data[ctl["name"]] = ctl["value"]  # a browser sends only the button that was clicked
            elif kind == "textarea":
                data[ctl["name"]] = ctl["value"].lstrip("\n")
            elif kind == "select":
                sel = next((o for o in ctl["options"] if o["selected"]), ctl["options"][0] if ctl["options"] else None)
                data[ctl["name"]] = sel["value"] if sel else ""
        for name, group in radios.items():
            chosen = next((r for r in group if r["checked"]), group[0])
            data[name] = chosen["value"]
        for key, value in (("title", req.title), ("desc", req.description), ("tags", " ".join(req.tags)), ("image", req.image)):
            ctl = self._by_id(form, key)
            if ctl is None:
                raise TrackerError(f"{self.name}: the upload form has no field #{self.ids[key]}; this site needs a field override")
            data[ctl["name"]] = value
        mi = next((c for c in form["controls"] if c["kind"] == "textarea" and c["id"] == "mediainfo" and c["name"]), None)
        if mi is not None and req.mediainfo:
            data[mi["name"]] = req.mediainfo
        cat = req.category or self.tracker.category
        cat_ctl = next((c for c in form["controls"] if c["kind"] == "select" and (c["id"] in CATEGORY_IDS or c["name"] in CATEGORY_IDS)), None)
        if cat_ctl is not None:
            if not cat:
                raise TrackerError(f"{self.name}: pick a category (tb tracker set {self.name} --category <id>)")
            if cat not in {o["value"] for o in cat_ctl["options"]}:
                raise TrackerError(f"{self.name}: category {cat!r} is not offered by the upload form")
            data[cat_ctl["name"]] = cat
        self._set_anonymous(form, data, req.anonymous or self.tracker.anonymous)
        return data

    @staticmethod
    def _upload_button(form: dict) -> dict | None:
        """The form can have several submit buttons (HappyFappy: "Load & check torrent" comes first and only checks the file).
        Take the one that uploads: id "post" or a label with "upload", else the last button that is not a check/preview."""
        buttons = [c for c in form["controls"] if c["kind"] == "input" and c["type"] == "submit" and c["name"]]
        for b in buttons:
            if b["id"] == "post" or "upload" in b["value"].lower():
                return b
        rest = [b for b in buttons if not any(w in (b["name"] + b["value"]).lower() for w in ("check", "preview"))]
        return (rest or buttons or [None])[-1]

    @staticmethod
    def _set_anonymous(form: dict, data: dict, anon: bool) -> None:
        """A radio/checkbox whose name mentions "anon"; else the userscript's rule: of the page's two radio buttons, the second means anonymous."""
        named = [c for c in form["controls"] if c["kind"] == "input" and c["type"] in ("radio", "checkbox") and "anon" in c["name"].lower()]
        if named:
            radios = [c for c in named if c["type"] == "radio"]
            if radios:
                pick = next((r for r in radios if (r["value"].lower() in ("1", "true", "yes", "on")) == anon), radios[1 if anon and len(radios) > 1 else 0])
                data[pick["name"]] = pick["value"]
            elif anon:
                data[named[0]["name"]] = named[0]["value"] or "on"
            else:
                data.pop(named[0]["name"], None)
            return
        radios = [c for c in form["controls"] if c["kind"] == "input" and c["type"] == "radio"]
        if len(radios) == 2:
            data[radios[1]["name"] if anon else radios[0]["name"]] = radios[1 if anon else 0]["value"]

    def publish(self, req: PublishRequest, dry_run: bool = False) -> PublishResult:
        with self._client() as c:
            p, url = self._get_page(c)
            form = _upload_form(p.forms)
            file_ctl = next((x for x in form["controls"] if x.get("type") == "file" and x["name"]), None)
            file_field = file_ctl["name"] if file_ctl else "file_input"
            data = self._build(form, req)
            sent = {k: (v if isinstance(v, str) else " ".join(v)) for k, v in data.items() if k.lower() not in ("auth", "authkey", "torrent_pass")}
            if dry_run:
                return PublishResult(dry_run=True, sent={**sent, file_field: req.torrent.name})
            action = urljoin(url, form["action"] or "upload.php")
            r = c.post(action, data=data, files={file_field: (req.torrent.name, req.torrent.read_bytes(), "application/x-bittorrent")})
        return self._result(r, action, sent)

    def _result(self, r: httpx.Response, action: str, sent: dict) -> PublishResult:
        if r.is_redirect:
            loc = urljoin(action, r.headers.get("location", ""))
            if "login" in urlparse(loc).path:
                raise LoginError(f"{self.name}: the session ended during the upload; log in again and refresh the cookie")
            m = re.search(r"torrents\.php\?[^#]*\bid=(\d+)", loc)
            if m:
                return PublishResult(torrent_url=loc, torrent_id=m.group(1), sent=sent)
            raise TrackerError(f"{self.name}: unexpected redirect to {loc}")
        msg = _error_text(r.text)
        if not msg and 'id="upload_table"' in r.text:
            msg = "the tracker showed the upload form again without a message (a required field may be empty, or the torrent was only checked)"
        raise TrackerError(f"{self.name}: upload rejected: {msg or f'HTTP {r.status_code}, no error text on the page'}")


def _error_text(html: str) -> str:
    m = re.search(r'<(?:div|p|span)[^>]*class="[^"]*(?:error|warning|alert)[^"]*"[^>]*>(.*?)</(?:div|p|span)>', html, re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", m.group(1)) if m else ""
    return " ".join(text.split())[:300]
