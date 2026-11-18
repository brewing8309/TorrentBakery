"""Optional login with a second factor (TOTP), standard library only.

Design:
- Passwords: scrypt with a random salt, parameters stored in the hash; compared in constant time. An unknown user name
  costs the same time as a wrong password.
- Second factor: RFC 6238 TOTP (SHA-1, 6 digits, 30 s, +-1 step). A step is accepted once (replay protection).
  Ten one-time recovery codes (80 bits each, stored only as SHA-256).
- Sessions are random 256-bit tokens kept in server memory (so logout and password change really end them, and a
  restart logs everybody out); the browser only holds an unguessable cookie (HttpOnly, SameSite=Strict, Secure on https).
- Between password and code a short-lived "pending" token exists; it is useless as a session.
- Failed logins are limited per client address (growing delay) and in total (window), so neither one address nor a
  distributed guess can run through codes. The limiter lives in memory and cannot be reset by a client.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import threading
import time
from urllib.parse import quote

from ..config import AuthCfg

SCRYPT_N, SCRYPT_R, SCRYPT_P = 2**16, 8, 1  # 64 MiB, ~0.1-0.2 s
MIN_PASSWORD = 12
SESSION_COOKIE = "tb_session"
PENDING_COOKIE = "tb_pending"
SESSION_MAX = 12 * 3600  # absolute lifetime
SESSION_IDLE = 2 * 3600
PENDING_TTL = 5 * 60
SETUP_TTL = 15 * 60


# ---- passwords -----------------------------------------------------------------------------------------------------
def _scrypt(password: str, salt: bytes, n: int, r: int, p: int) -> bytes:
    return hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32, maxmem=256 * 1024 * 1024)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    h = _scrypt(password, salt, SCRYPT_N, SCRYPT_R, SCRYPT_P)
    return "scrypt${}${}${}${}${}".format(SCRYPT_N, SCRYPT_R, SCRYPT_P, salt.hex(), h.hex())


_DUMMY = hash_password(secrets.token_hex(16))


def verify_password(password: str, stored: str) -> bool:
    try:
        kind, n, r, p, salt, h = stored.split("$")
        if kind != "scrypt":
            return False
        return hmac.compare_digest(_scrypt(password, bytes.fromhex(salt), int(n), int(r), int(p)), bytes.fromhex(h))
    except (ValueError, TypeError):
        return False


def check_password_policy(password: str, username: str = "") -> str:
    """'' if acceptable, else the reason."""
    if len(password) < MIN_PASSWORD:
        return f"The password needs at least {MIN_PASSWORD} characters."
    if len(password) > 256:
        return "The password is too long (256 characters at most)."
    if username and password.lower() == username.lower():
        return "The password must not be the user name."
    if len(set(password)) < 5:
        return "The password is too repetitive."
    return ""


def check_credentials(a: AuthCfg, username: str, password: str) -> bool:
    ok_user = hmac.compare_digest(username.encode(), a.username.encode())
    ok_pass = verify_password(password, a.password_hash if ok_user else _DUMMY)  # same work either way
    return ok_user and ok_pass


# ---- TOTP ----------------------------------------------------------------------------------------------------------
def new_totp_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode()  # 160 bit, no padding needed (20 bytes)


def _hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.replace(" ", "").upper())
    d = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = d[-1] & 15
    return f"{(struct.unpack('>I', d[o:o + 4])[0] & 0x7FFFFFFF) % 10**6:06d}"


def totp_match(secret: str, code: str, now: float | None = None, after_step: int = 0) -> int:
    """The step the code belongs to (within +-1 step), 0 if it does not fit or that step was already used."""
    code = code.replace(" ", "").replace("-", "")
    if len(code) != 6 or not code.isdigit():
        return 0
    step, found = int((now if now is not None else time.time()) // 30), 0
    for s in (step - 1, step, step + 1):  # no early exit: constant work
        if hmac.compare_digest(_hotp(secret, s), code) and s > after_step:
            found = s
    return found


def totp_uri(secret: str, username: str, issuer: str = "TorrentBakery") -> str:
    return f"otpauth://totp/{quote(issuer)}:{quote(username)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def group(secret: str) -> str:
    return " ".join(secret[i:i + 4] for i in range(0, len(secret), 4))


def new_recovery_codes(n: int = 10) -> list[str]:
    return ["-".join(c[i:i + 4] for i in range(0, 16, 4)) for c in (base64.b32encode(secrets.token_bytes(10)).decode() for _ in range(n))]


def _rc_hash(code: str) -> str:
    return hashlib.sha256(code.replace("-", "").replace(" ", "").upper().encode()).hexdigest()


def hash_recovery(codes: list[str]) -> list[str]:
    return [_rc_hash(c) for c in codes]


def use_recovery(a: AuthCfg, code: str) -> bool:
    """Consumes the code if it is one of the unused ones."""
    h, hit = _rc_hash(code), ""
    for r in a.recovery:  # no early exit
        if hmac.compare_digest(r, h):
            hit = r
    if hit:
        a.recovery.remove(hit)
    return bool(hit)


# ---- sessions, pending logins, throttling (memory only) ------------------------------------------------------------
def _key(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()  # a memory dump does not hand out usable tokens


class AuthState:
    def __init__(self, clock=time.time):
        self.clock, self.lock = clock, threading.Lock()
        self.sessions: dict[str, list[float]] = {}  # key -> [created, last seen]
        self.pending: dict[str, list[float]] = {}  # key -> [expires, wrong codes so far]
        self.setup: tuple[str, float] | None = None  # (TOTP secret being set up, expires)
        self.hashing = threading.Semaphore(2)  # scrypt is memory-hungry: never more than two at once
        self.flash: tuple[str, bool] | None = None  # one message for the next settings page (message, ok)
        self.fails: dict[str, list[float]] = {}  # client -> failure times
        self.all_fails: list[float] = []

    # sessions
    def start_session(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[_key(token)] = [self.clock(), self.clock()]
        return token

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        now = self.clock()
        with self.lock:
            s = self.sessions.get(_key(token))
            if s and now - s[0] < SESSION_MAX and now - s[1] < SESSION_IDLE:
                s[1] = now
                return True
            if s:
                del self.sessions[_key(token)]
        return False

    def end_session(self, token: str | None) -> None:
        with self.lock:
            self.sessions.pop(_key(token or ""), None)

    def end_all(self) -> None:
        with self.lock:
            self.sessions.clear()
            self.pending.clear()
            self.setup = None

    # between password and second factor
    def start_pending(self) -> str:
        token = secrets.token_urlsafe(32)
        with self.lock:
            now = self.clock()
            self.pending = {k: v for k, v in self.pending.items() if v[0] > now}
            self.pending[_key(token)] = [now + PENDING_TTL, 0]
        return token

    def pending_valid(self, token: str | None) -> bool:
        with self.lock:
            return bool(token) and self.pending.get(_key(token), [0, 0])[0] > self.clock()

    def pending_failed(self, token: str | None) -> None:
        """Three wrong codes end the pending login: the password has to be entered again."""
        with self.lock:
            p = self.pending.get(_key(token or ""))
            if p:
                p[1] += 1
                if p[1] >= 3:
                    del self.pending[_key(token or "")]

    def end_pending(self, token: str | None) -> None:
        with self.lock:
            self.pending.pop(_key(token or ""), None)

    # 2FA setup in progress
    def begin_setup(self) -> str:
        secret = new_totp_secret()
        with self.lock:
            self.setup = (secret, self.clock() + SETUP_TTL)
        return secret

    def setup_secret(self) -> str:
        with self.lock:
            return self.setup[0] if self.setup and self.setup[1] > self.clock() else ""

    def end_setup(self) -> None:
        with self.lock:
            self.setup = None

    # throttling: per client 1 s doubling after 3 failures (cap 15 min); all clients together 20 failures per 15 min
    def wait_seconds(self, client: str) -> int:
        now = self.clock()
        with self.lock:
            self.all_fails = [t for t in self.all_fails if now - t < 900]
            mine = [t for t in self.fails.get(client, []) if now - t < 900]
            self.fails[client] = mine
            wait = 0
            if len(self.all_fails) >= 20:
                wait = int(900 - (now - self.all_fails[0])) + 1
            if len(mine) >= 3:
                wait = max(wait, int(min(900, 2 ** (len(mine) - 2)) - (now - mine[-1])) + 1)
            return max(wait, 0)

    def failed(self, client: str) -> None:
        with self.lock:
            now = self.clock()
            self.fails.setdefault(client, []).append(now)
            self.all_fails.append(now)
            if len(self.fails) > 1000:  # a flood of addresses must not grow memory without bound
                self.fails = {c: t for c, t in self.fails.items() if t and now - t[-1] < 900}

    def succeeded(self, client: str) -> None:
        with self.lock:
            self.fails.pop(client, None)


def safe_next(target: str | None) -> str:
    """Only local paths: no scheme, no host, no protocol-relative or backslash tricks."""
    if target and target.startswith("/") and not target.startswith(("//", "/\\")) and "\\" not in target and "\n" not in target and "\r" not in target:
        return target
    return "/"
