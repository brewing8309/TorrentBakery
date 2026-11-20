"""Hardening that is not part of the login itself: CSRF and DNS rebinding without a login, secrets bound to their site, safe output names."""
import httpx
import pytest
from fastapi.testclient import TestClient

from torrentbakery import config as config_mod
from torrentbakery.config import Tracker
from torrentbakery.net import keep_on_site, same_site
from torrentbakery.pipeline import slug
from torrentbakery.web.app import create_app


class Recorder:
    """A transport that remembers every request and answers like a site that refuses everything."""

    def __init__(self):
        self.seen: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(401, text="no")

    def sent(self, secret: str) -> bool:
        return any(secret in str(r.headers) or secret in r.content.decode(errors="ignore") for r in self.seen)


@pytest.fixture
def setup(tmp_path):
    cfg = config_mod.Config()
    cfg.stash.url, cfg.stash.api_key = "http://stash.lan:9999", "STASHKEY"
    cfg.qbit.url, cfg.qbit.username, cfg.qbit.password = "http://qbit.lan:8080", "admin", "QBITPASS"
    cfg.trackers["bp"] = Tracker(site="bitporn", cookie="BPTOKEN", session_cookie="remember_web_x=BPSESSION")
    config_mod.save(cfg, tmp_path / "config.toml")
    rec = Recorder()
    app = create_app(tmp_path / "out", tmp_path / "config.toml", transport=httpx.MockTransport(rec), qbit_transport=httpx.MockTransport(rec))
    return TestClient(app, base_url="http://10.0.0.5:8080", follow_redirects=False), rec, tmp_path / "config.toml"


def test_cross_site_post_refused_without_login(setup):
    c, rec, path = setup
    r = c.post("/settings/qbit/test", data={"url": "http://evil.example"}, headers={"Origin": "https://evil.example"})
    assert r.status_code == 403 and not rec.seen
    r = c.post("/settings/stash", data={"url": "http://evil.example"}, headers={"Sec-Fetch-Site": "cross-site"})
    assert r.status_code == 403 and config_mod.load(path).stash.url == "http://stash.lan:9999"
    r = c.post("/settings/auth/enable", data={"username": "evil", "password": "x" * 20, "password2": "x" * 20}, headers={"Origin": "http://evil.example"})
    assert r.status_code == 403 and not config_mod.load(path).auth.enabled
    assert c.post("/settings/default-host", data={"host": ""}, headers={"Origin": "http://10.0.0.5:8080", "Sec-Fetch-Site": "same-origin"}).status_code == 303


def test_dns_rebinding_names_refused_without_login(setup):
    c, _, _ = setup
    assert c.get("/busy", headers={"Host": "rebind.evil.example:8080"}).status_code == 421
    for ok in ("10.0.0.5:8080", "[fd00::5]:8080", "localhost:8080", "bakery:8080", "tb.test"):
        assert c.get("/busy", headers={"Host": ok}).status_code == 200, ok


def test_allowed_hosts_set_in_settings(setup):
    c, _, path = setup
    assert c.get("/busy", headers={"Host": "bakery.example.org"}).status_code == 421
    r = c.post("/settings/hosts", data={"hosts": "https://Bakery.Example.org:8443/x\nfoo.lan, foo.lan"})
    assert r.status_code == 303 and config_mod.load(path).allowed_hosts == ["bakery.example.org", "foo.lan"]
    assert c.get("/busy", headers={"Host": "bakery.example.org"}).status_code == 200
    assert c.get("/busy", headers={"Host": "sub.foo.lan"}).status_code == 200
    assert c.get("/busy", headers={"Host": "evil.example"}).status_code == 421
    c.post("/settings/hosts", data={"hosts": "not a name"})
    assert config_mod.load(path).allowed_hosts == ["bakery.example.org", "foo.lan"]


def test_saved_secrets_only_go_to_their_own_address(setup):
    c, rec, path = setup
    c.post("/settings/qbit/test", data={"url": "http://evil.example", "username": "admin"})
    assert rec.seen and not rec.sent("QBITPASS")
    rec.seen.clear()
    c.post("/settings/tracker/categories", data={"name": "bp", "site": "bitporn", "base_url": "http://evil.example"})
    assert not rec.sent("BPTOKEN") and not rec.sent("BPSESSION")  # without its token the probe does not even go out
    # editing the address drops what was saved for the old one; the same address keeps it
    c.post("/settings/tracker", data={"name": "bp", "site": "bitporn", "base_url": "http://evil.example"})
    t = config_mod.load(path).trackers["bp"]
    assert t.cookie == "" and t.session_cookie == ""
    c.post("/settings/stash", data={"url": "http://stash.lan:9999/"})
    assert config_mod.load(path).stash.api_key == "STASHKEY"
    c.post("/settings/stash", data={"url": "http://evil.example"})
    assert config_mod.load(path).stash.api_key == ""


def test_no_api_browser_and_security_headers(setup):
    c, _, _ = setup
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    h = c.get("/").headers
    assert "frame-ancestors 'none'" in h["content-security-policy"] and h["x-frame-options"] == "DENY"


@pytest.mark.parametrize("name", [".", "..", "...", "_.._", ""])
def test_slug_never_leaves_the_output_root(name):
    assert slug(name) == "release"
    assert slug("a..b") == "a..b" and slug(".nfo") == ".nfo"


def test_secret_headers_stay_on_their_site():
    seen = []

    def answer(req):
        seen.append((req.url.host, req.headers.get("ApiKey"), req.headers.get("Cookie")))
        return httpx.Response(302, headers={"location": "https://cdn.evil.example/x"}) if req.url.host == "stash.lan" else httpx.Response(200)

    with httpx.Client(transport=httpx.MockTransport(answer), follow_redirects=True, headers={"ApiKey": "K", "Cookie": "C"},
                      event_hooks=keep_on_site("http://stash.lan", "ApiKey", "Cookie")) as client:
        client.get("http://stash.lan/scene/1/preview")
    assert seen == [("stash.lan", "K", "C"), ("cdn.evil.example", None, None)]
    assert same_site("https://www.site.org/x", "https://site.org") and not same_site("http://site.org", "https://site.org")
