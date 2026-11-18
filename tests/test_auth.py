import time

import pytest
from fastapi.testclient import TestClient

from torrentbakery import config as config_mod
from torrentbakery.web import auth
from torrentbakery.web.app import create_app

PW = "correct horse battery"


@pytest.fixture(autouse=True)
def fast_scrypt(monkeypatch):
    monkeypatch.setattr(auth, "SCRYPT_N", 2**4)


def client(tmp_path, **kw):
    return TestClient(create_app(tmp_path / "out", tmp_path / "config.toml"), base_url="http://tb.test", follow_redirects=False, **kw)


def second(c):
    """Another browser talking to the same running app (its sessions and throttling are shared)."""
    return TestClient(c.app, base_url="http://tb.test", follow_redirects=False)


def reset_throttle(c):
    c.app.state.auth.fails.clear()
    c.app.state.auth.all_fails.clear()


def enable(c):
    r = c.post("/settings/auth/enable", data={"username": "max", "password": PW, "password2": PW})
    assert r.status_code == 303 and auth.SESSION_COOKIE in r.cookies
    return r


def totp_on(c, tmp_path):
    c.post("/settings/auth/2fa/start")
    secret = auth.AuthState and c.app.state.auth.setup_secret()
    step = int(time.time() // 30)
    r = c.post("/settings/auth/2fa/confirm", data={"code": auth._hotp(secret, step)})
    assert r.status_code == 200 and "Recovery codes" in r.text
    codes = [ln.strip() for ln in r.text.split("<pre")[1].split(">", 1)[1].split("</pre>")[0].splitlines() if ln.strip()]
    return secret, step, codes


def test_password_hash_and_policy():
    h = auth.hash_password(PW)
    assert PW not in h and auth.verify_password(PW, h) and not auth.verify_password(PW + "x", h) and not auth.verify_password(PW, "garbage")
    assert auth.hash_password(PW) != h  # random salt
    assert auth.check_password_policy("short") and auth.check_password_policy("aaaaaaaaaaaaaaaa") and auth.check_password_policy("maxmaxmaxmax", "maxmaxmaxmax")
    assert not auth.check_password_policy(PW)


def test_totp_rfc6238_vector_and_replay():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # RFC 6238 test key, T=59 -> 94287082 (8 digits), so 287082
    assert auth.totp_match(secret, "287082", now=59) == 1
    assert auth.totp_match(secret, "287 082", now=59) == 1
    assert auth.totp_match(secret, "287082", now=59, after_step=1) == 0  # replay
    assert auth.totp_match(secret, "287082", now=59 + 120) == 0  # expired
    assert auth.totp_match(secret, "28708", now=59) == 0 and auth.totp_match(secret, "abcdef", now=59) == 0


def test_recovery_codes_work_once():
    codes = auth.new_recovery_codes()
    a = config_mod.AuthCfg(recovery=auth.hash_recovery(codes))
    assert all(c not in "".join(a.recovery) for c in codes)
    assert auth.use_recovery(a, codes[0].lower()) and not auth.use_recovery(a, codes[0]) and len(a.recovery) == 9


def test_safe_next():
    assert auth.safe_next("/jobs/1?x=2") == "/jobs/1?x=2"
    for bad in ("//evil.example", "https://evil.example", "/\\evil", "evil", "", None, "/a\r\nSet-Cookie: x"):
        assert auth.safe_next(bad) == "/"


def test_throttling():
    now = [1000.0]
    s = auth.AuthState(clock=lambda: now[0])
    assert s.wait_seconds("a") == 0
    for _ in range(3):
        s.failed("a")
    assert s.wait_seconds("a") > 0 and s.wait_seconds("b") == 0
    now[0] += 3
    assert s.wait_seconds("a") == 0  # delay doubles but ends
    for _ in range(20):
        s.failed(f"x{_}")
    assert s.wait_seconds("fresh") > 0  # all clients together
    now[0] += 901
    assert s.wait_seconds("fresh") == 0


def test_session_expiry():
    now = [0.0]
    s = auth.AuthState(clock=lambda: now[0])
    t = s.start_session()
    assert s.valid(t) and not s.valid("other") and not s.valid(None)
    now[0] += auth.SESSION_IDLE - 1
    assert s.valid(t)  # activity extends the idle time
    now[0] += auth.SESSION_IDLE + 1
    assert not s.valid(t)


def test_open_until_enabled_then_guarded(tmp_path):
    c = client(tmp_path)
    assert c.get("/").status_code == 200 and c.get("/settings").status_code == 200
    enable(c)
    assert c.get("/settings").status_code == 200  # this browser got a session
    other = second(c)
    r = other.get("/settings?x=1", headers={"accept": "text/html"})
    assert r.status_code == 303 and r.headers["location"] == "/login?next=/settings%3Fx%3D1"
    assert other.post("/settings/stash", data={"url": "http://x"}).status_code == 401
    assert other.get("/jobs/nope/state", headers={"accept": "application/json"}).status_code == 401
    assert other.get("/static/brand/favicon.svg").status_code == 200 and other.get("/login").status_code == 200
    assert config_mod.load(tmp_path / "config.toml").stash.url == ""
    h = other.get("/login").headers
    assert h["x-frame-options"] == "DENY" and h["x-content-type-options"] == "nosniff"


def test_login_cookie_flags_logout_and_secrets_hidden(tmp_path):
    c = client(tmp_path)
    enable(c)
    cfgtext = (tmp_path / "config.toml").read_text()
    assert PW not in cfgtext and "scrypt$" in cfgtext and oct((tmp_path / "config.toml").stat().st_mode & 0o777) == "0o600"
    assert "scrypt$" not in c.get("/settings").text
    d = second(c)
    assert d.post("/login", data={"username": "max", "password": "wrong password!"}).status_code == 401
    assert d.post("/login", data={"username": "nobody", "password": PW}).status_code == 401
    r = d.post("/login", data={"username": "max", "password": PW, "next": "//evil.example"})
    assert r.status_code == 303 and r.headers["location"] == "/"
    sc = r.headers["set-cookie"].lower()
    assert "httponly" in sc and "samesite=strict" in sc and "secure" not in sc
    assert d.get("/").status_code == 200
    token = d.cookies[auth.SESSION_COOKIE]
    assert d.post("/logout").status_code == 303
    d.cookies.set(auth.SESSION_COOKIE, token)
    assert d.get("/", headers={"accept": "text/html"}).status_code == 303  # the old token is dead server-side
    https = TestClient(c.app, base_url="https://tb.test", follow_redirects=False)
    assert "secure" in https.post("/login", data={"username": "max", "password": PW}).headers["set-cookie"].lower()


def test_cross_site_post_refused(tmp_path):
    c = client(tmp_path)
    enable(c)
    r = c.post("/settings/stash", data={"url": "http://evil"}, headers={"origin": "https://evil.example"})
    assert r.status_code == 403 and config_mod.load(tmp_path / "config.toml").stash.url == ""
    assert c.post("/settings/stash", data={"url": "http://ok"}, headers={"origin": "http://tb.test"}).status_code == 303


def test_login_is_throttled(tmp_path):
    c = client(tmp_path)
    enable(c)
    d = second(c)
    for _ in range(3):
        assert d.post("/login", data={"username": "max", "password": "wrong password!"}).status_code == 401
    r = d.post("/login", data={"username": "max", "password": PW})  # even the right one waits
    assert r.status_code == 429 and "Try again" in r.text


def test_password_policy_and_change(tmp_path):
    c = client(tmp_path)
    assert c.post("/settings/auth/enable", data={"username": "max", "password": "short", "password2": "short"}).status_code == 303
    assert not config_mod.load(tmp_path / "config.toml").auth.enabled
    assert c.post("/settings/auth/enable", data={"username": "max", "password": PW, "password2": PW + "!"}).status_code == 303
    assert not config_mod.load(tmp_path / "config.toml").auth.enabled
    enable(c)
    other = second(c)
    other.post("/login", data={"username": "max", "password": PW})
    assert other.get("/", headers={"accept": "text/html"}).status_code == 200
    c.post("/settings/auth/password", data={"current": "not it, no no", "password": "another long phrase", "password2": "another long phrase"})
    assert auth.verify_password(PW, config_mod.load(tmp_path / "config.toml").auth.password_hash)
    c.post("/settings/auth/password", data={"current": PW, "password": "another long phrase", "password2": "another long phrase"})
    assert auth.verify_password("another long phrase", config_mod.load(tmp_path / "config.toml").auth.password_hash)
    assert other.get("/", headers={"accept": "text/html"}).status_code == 303  # other sessions ended
    assert c.get("/settings").status_code == 200  # this one continues


def test_two_factor_flow(tmp_path):
    c = client(tmp_path)
    enable(c)
    secret, step, codes = totp_on(c, tmp_path)
    assert len(codes) == 10
    cfgtext = (tmp_path / "config.toml").read_text()
    assert all(x not in cfgtext for x in codes) and "totp_secret" in cfgtext
    page = c.get("/settings").text
    assert secret not in page and secret.replace("", " ") not in page and "10 one-time recovery codes" in page

    d = second(c)
    r = d.post("/login", data={"username": "max", "password": PW})
    assert r.status_code == 303 and r.headers["location"].startswith("/login/code") and auth.PENDING_COOKIE in d.cookies
    assert d.get("/", headers={"accept": "text/html"}).status_code == 303  # a password alone is no session
    assert d.post("/login/code", data={"code": "000000"}).status_code == 401
    assert d.post("/login/code", data={"code": auth._hotp(secret, step)}).status_code == 401  # replay of the confirm code
    assert d.post("/login/code", data={"code": "000000"}).status_code in (303, 401, 429)  # third failure kills the pending login
    d = second(c)
    reset_throttle(c)  # every test client has the same address
    d.post("/login", data={"username": "max", "password": PW})
    r = d.post("/login/code", data={"code": auth._hotp(secret, step + 1)})
    assert r.status_code == 303 and d.get("/", headers={"accept": "text/html"}).status_code == 200

    reset_throttle(c)
    e = second(c)  # recovery code, once
    e.post("/login", data={"username": "max", "password": PW})
    assert e.post("/login/code", data={"code": codes[0]}).status_code == 303
    reset_throttle(c)
    f = second(c)
    f.post("/login", data={"username": "max", "password": PW})
    assert f.post("/login/code", data={"code": codes[0]}).status_code == 401
    assert len(config_mod.load(tmp_path / "config.toml").auth.recovery) == 9


def test_disable_needs_password_and_code(tmp_path):
    c = client(tmp_path)
    enable(c)
    secret, step, _ = totp_on(c, tmp_path)
    c.post("/settings/auth/2fa/disable", data={"current": PW, "code": "123456"})
    assert config_mod.load(tmp_path / "config.toml").auth.totp
    reset_throttle(c)
    c.post("/settings/auth/2fa/disable", data={"current": "bad password here", "code": auth._hotp(secret, step + 1)})
    assert config_mod.load(tmp_path / "config.toml").auth.totp
    reset_throttle(c)
    c.post("/settings/auth/2fa/disable", data={"current": PW, "code": auth._hotp(secret, step + 1)})
    a = config_mod.load(tmp_path / "config.toml").auth
    assert a.enabled and not a.totp and not a.recovery
    c.post("/settings/auth/disable", data={"current": PW})
    assert not config_mod.load(tmp_path / "config.toml").auth.enabled
    assert second(c).get("/settings").status_code == 200
