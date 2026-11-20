"""Login pages, the guard in front of every route and the security settings (see auth.py for the design)."""
from __future__ import annotations

import ipaddress
import logging
import os
import threading
from pathlib import Path
from urllib.parse import quote, urlparse

from fastapi import FastAPI, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from .. import config as config_mod
from . import auth

log = logging.getLogger("torrentbakery.auth")
PUBLIC = ("/login", "/static/")
HEADERS = {"X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff", "Referrer-Policy": "same-origin", "Cross-Origin-Opener-Policy": "same-origin",
           # the pages use inline scripts and styles; images come from the image hosts. No foreign scripts, no framing, forms only to us.
           "Content-Security-Policy": "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' https: http: data: blob:; "
                                      "object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'"}


def client_key(host: str) -> str:
    """Whom failed logins are counted against: the address, for IPv6 its /64 (one machine easily holds a whole /64)."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host
    if ip.version == 6 and not ip.ipv4_mapped:
        return str(ipaddress.ip_network(f"{host}/64", strict=False).network_address)
    return host


def allowed_hosts() -> list[str]:
    return [h.strip().lower() for h in os.environ.get("TB_ALLOWED_HOSTS", "").split(",") if h.strip()]


def host_ok(host: str | None) -> bool:
    """Without a login, only names a DNS-rebinding page cannot take over: IP addresses, localhost, single-label LAN names and TB_ALLOWED_HOSTS."""
    name = (urlparse("//" + (host or "")).hostname or "").lower()
    if not name:
        return False
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        pass
    return name == "localhost" or "." not in name or any(name == a or name.endswith("." + a.lstrip(".")) for a in allowed_hosts())


def cross_site(request: Request) -> bool:
    """A request another site made the browser send (CSRF). Refused for every changing method, login or not."""
    site = request.headers.get("sec-fetch-site")
    if site and site not in ("same-origin", "none"):
        return True
    origin = request.headers.get("origin")
    return bool(origin) and urlparse(origin).netloc not in (request.headers.get("host"), request.headers.get("x-forwarded-host"))


def install(app: FastAPI, templates: Jinja2Templates, config_path: Path | None) -> auth.AuthState:
    state = app.state.auth = auth.AuthState()
    cfg = lambda: config_mod.load(config_path)  # noqa: E731
    client = lambda r: client_key(r.client.host) if r.client else "?"  # noqa: E731
    factor = threading.Lock()  # a code (or recovery code) is checked and marked as used in one go: two requests cannot both use it

    def cookie(resp: Response, request: Request, name: str, value: str, max_age: int) -> None:
        secure = request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"
        resp.set_cookie(name, value, max_age=max_age, path="/", httponly=True, samesite="strict", secure=secure)

    def page(request: Request, name: str, ctx: dict, status: int = 200) -> Response:
        r = templates.TemplateResponse(request, name, {"bare": True, **ctx}, status_code=status)
        r.headers["Cache-Control"] = "no-store"
        return r

    @app.middleware("http")
    async def guard(request: Request, call_next):
        a = cfg().auth
        request.state.auth_on = a.enabled
        path = request.url.path
        resp: Response | None = None
        if not a.enabled and not host_ok(request.headers.get("host")):
            resp = Response(f"TorrentBakery does not answer to the name {request.headers.get('host')!r} while the login is off (DNS rebinding). "
                            "Open it by its IP address, turn the login on, or add the name to TB_ALLOWED_HOSTS in /etc/default/torrentbakery.\n",
                            status_code=421, media_type="text/plain")
        elif request.method not in ("GET", "HEAD", "OPTIONS") and cross_site(request):  # CSRF: with the login on top of SameSite=Strict
            resp = JSONResponse({"detail": "cross-site request refused"}, status_code=403)
        if a.enabled:
            if resp is None and not path.startswith(PUBLIC) and not state.valid(request.cookies.get(auth.SESSION_COOKIE)):
                if request.method == "GET" and "text/html" in request.headers.get("accept", "text/html"):
                    resp = RedirectResponse("/login?next=" + quote(path + ("?" + request.url.query if request.url.query else ""), safe="/"), status_code=303)
                else:
                    resp = JSONResponse({"detail": "login required"}, status_code=401)
        resp = resp or await call_next(request)
        for k, v in HEADERS.items():
            resp.headers.setdefault(k, v)
        if a.enabled and not path.startswith("/static/"):
            resp.headers.setdefault("Cache-Control", "no-store")  # nothing private stays in a shared cache
        return resp

    def finish(request: Request, next_url: str) -> Response:
        """Password (and code) accepted: a fresh session token, never one the browser brought along."""
        state.end_session(request.cookies.get(auth.SESSION_COOKIE))
        state.end_pending(request.cookies.get(auth.PENDING_COOKIE))
        state.succeeded(client(request))
        resp = RedirectResponse(auth.safe_next(next_url), status_code=303)
        cookie(resp, request, auth.SESSION_COOKIE, state.start_session(), auth.SESSION_MAX)
        resp.delete_cookie(auth.PENDING_COOKIE, path="/")
        return resp

    def throttled(request: Request) -> str:
        w = state.wait_seconds(client(request))
        return f"Too many failed attempts. Try again in {w} seconds." if w else ""

    @app.get("/login")
    def login_form(request: Request, next: str = "/"):
        a = cfg().auth
        if not a.enabled or state.valid(request.cookies.get(auth.SESSION_COOKIE)):
            return RedirectResponse(auth.safe_next(next), status_code=303)
        return page(request, "login.html", {"step": "password", "next": auth.safe_next(next), "error": ""})

    @app.post("/login")
    def login(request: Request, username: str = Form(""), password: str = Form(""), next: str = Form("/")):
        a = cfg().auth
        if not a.enabled:
            return RedirectResponse("/", status_code=303)
        ctx = {"step": "password", "next": auth.safe_next(next)}
        if msg := throttled(request):
            return page(request, "login.html", {**ctx, "error": msg}, 429)
        with state.hashing:
            ok = auth.check_credentials(a, username.strip(), password[:512])
        if not ok:
            state.failed(client(request))
            log.warning("failed login for %r from %s", username[:40], client(request))
            return page(request, "login.html", {**ctx, "error": "Wrong user name or password."}, 401)
        if not a.totp:
            return finish(request, next)
        resp = RedirectResponse("/login/code?next=" + quote(auth.safe_next(next), safe="/"), status_code=303)
        cookie(resp, request, auth.PENDING_COOKIE, state.start_pending(), auth.PENDING_TTL)
        return resp

    @app.get("/login/code")
    def code_form(request: Request, next: str = "/"):
        if not state.pending_valid(request.cookies.get(auth.PENDING_COOKIE)):
            return RedirectResponse("/login?next=" + quote(auth.safe_next(next), safe="/"), status_code=303)
        return page(request, "login.html", {"step": "code", "next": auth.safe_next(next), "error": ""})

    @app.post("/login/code")
    def code(request: Request, code: str = Form(""), next: str = Form("/")):
        pending = request.cookies.get(auth.PENDING_COOKIE)
        if not state.pending_valid(pending):
            return RedirectResponse("/login?next=" + quote(auth.safe_next(next), safe="/"), status_code=303)
        ctx = {"step": "code", "next": auth.safe_next(next)}
        if msg := throttled(request):
            return page(request, "login.html", {**ctx, "error": msg}, 429)
        with factor:
            c = cfg()
            step = auth.totp_match(c.auth.totp_secret, code, after_step=c.auth.totp_last_step)
            if step:
                c.auth.totp_last_step = step
            elif len(code.replace("-", "").replace(" ", "")) > 6 and auth.use_recovery(c.auth, code[:64]):
                log.warning("recovery code used from %s (%d left)", client(request), len(c.auth.recovery))
            else:
                state.failed(client(request))
                state.pending_failed(pending)
                log.warning("failed second factor from %s", client(request))
                if not state.pending_valid(pending):
                    return RedirectResponse("/login", status_code=303)
                return page(request, "login.html", {**ctx, "error": "That code is wrong or was already used."}, 401)
            config_mod.save(c, config_path)
        return finish(request, next)

    @app.post("/logout")
    def logout(request: Request):
        state.end_session(request.cookies.get(auth.SESSION_COOKIE))
        resp = RedirectResponse("/login", status_code=303)
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    # ---- settings ----------------------------------------------------------------------------------------------------
    def back(msg: str = "", ok: bool = True) -> Response:
        if msg:
            state.flash = (msg, ok)
        return RedirectResponse("/settings#auth", status_code=303)

    def reauth(request: Request, c, password: str, code_: str | None = None) -> str:
        """'' if the password (and, when asked for, a fresh code) is right; counts as a failed login otherwise."""
        if msg := throttled(request):
            return msg
        with state.hashing:
            ok = auth.verify_password(password[:512], c.auth.password_hash)
        err = "" if ok else "The current password is wrong."
        if ok and code_ is not None and c.auth.totp:
            step = auth.totp_match(c.auth.totp_secret, code_, after_step=c.auth.totp_last_step)
            if step:
                c.auth.totp_last_step = step
            else:
                err = "The code is wrong or was already used (a code works once: wait for the next one)."
        if err:
            state.failed(client(request))
            log.warning("failed re-authentication from %s", client(request))
        return err

    def renew(request: Request, resp: Response) -> Response:
        """After a change of credentials every other session ends; this browser gets a new one."""
        state.end_all()
        cookie(resp, request, auth.SESSION_COOKIE, state.start_session(), auth.SESSION_MAX)
        return resp

    @app.post("/settings/auth/enable")
    def enable(request: Request, username: str = Form(...), password: str = Form(...), password2: str = Form(...)):
        c = cfg()
        username = username.strip()
        if c.auth.enabled:
            return back("Login is already on.", False)
        if not username or len(username) > 64 or not username.isprintable():
            return back("Enter a user name (up to 64 characters).", False)
        if password != password2:
            return back("The two passwords differ.", False)
        if msg := auth.check_password_policy(password, username):
            return back(msg, False)
        with state.hashing:
            c.auth = config_mod.AuthCfg(username=username, password_hash=auth.hash_password(password))
        config_mod.save(c, config_path)
        state.flash = ("Login is on. Set up two-factor authentication below to protect it further.", True)
        return renew(request, RedirectResponse("/settings#auth", status_code=303))

    @app.post("/settings/auth/password")
    def change_password(request: Request, current: str = Form(...), password: str = Form(...), password2: str = Form(...)):
        c = cfg()
        if not c.auth.enabled:
            return back()
        if password != password2:
            return back("The two new passwords differ.", False)
        if msg := auth.check_password_policy(password, c.auth.username):
            return back(msg, False)
        if err := reauth(request, c, current):
            return back(err, False)
        with state.hashing:
            c.auth.password_hash = auth.hash_password(password)
        config_mod.save(c, config_path)
        state.flash = ("Password changed. All other sessions were logged out.", True)
        return renew(request, RedirectResponse("/settings#auth", status_code=303))

    @app.post("/settings/auth/2fa/start")
    def totp_start():
        c = cfg()
        if c.auth.enabled and not c.auth.totp:
            state.begin_setup()
        return back()

    @app.post("/settings/auth/2fa/confirm")
    def totp_confirm(request: Request, code: str = Form("")):
        c = cfg()
        secret = state.setup_secret()
        if not c.auth.enabled or c.auth.totp or not secret:
            return back("The setup expired: start again.", False)
        if msg := throttled(request):
            return back(msg, False)
        step = auth.totp_match(secret, code)
        if not step:
            state.failed(client(request))
            return back("That code does not fit. Check the phone's clock and try the next code.", False)
        codes = auth.new_recovery_codes()
        c.auth.totp_secret, c.auth.totp_last_step, c.auth.recovery = secret, step, auth.hash_recovery(codes)
        config_mod.save(c, config_path)
        state.end_setup()
        return renew(request, page(request, "recovery.html", {"codes": codes, "what": "Two-factor authentication is on."}))

    @app.post("/settings/auth/recovery")
    def recovery_again(request: Request, current: str = Form(...), code: str = Form("")):
        c = cfg()
        if not c.auth.totp:
            return back()
        if err := reauth(request, c, current, code):
            return back(err, False)
        codes = auth.new_recovery_codes()
        c.auth.recovery = auth.hash_recovery(codes)
        config_mod.save(c, config_path)
        return page(request, "recovery.html", {"codes": codes, "what": "New recovery codes. The old ones no longer work."})

    @app.post("/settings/auth/2fa/disable")
    def totp_disable(request: Request, current: str = Form(...), code: str = Form("")):
        c = cfg()
        if not c.auth.totp:
            return back()
        if err := reauth(request, c, current, code):
            return back(err, False)
        c.auth.totp_secret, c.auth.totp_last_step, c.auth.recovery = "", 0, []
        config_mod.save(c, config_path)
        state.flash = ("Two-factor authentication is off.", True)
        return renew(request, RedirectResponse("/settings#auth", status_code=303))

    @app.post("/settings/auth/disable")
    def disable(request: Request, current: str = Form(...), code: str = Form("")):
        c = cfg()
        if not c.auth.enabled:
            return back()
        if err := reauth(request, c, current, code):
            return back(err, False)
        c.auth = config_mod.AuthCfg()
        config_mod.save(c, config_path)
        state.end_all()
        state.flash = ("Login is off: the interface is open again.", True)
        resp = RedirectResponse("/settings#auth", status_code=303)
        resp.delete_cookie(auth.SESSION_COOKIE, path="/")
        return resp

    return state
