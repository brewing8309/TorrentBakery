# 🔐 Login & 2FA

[← Documentation](README.md)

The login is **optional and off by default**. Without it, anyone who reaches the port can use TorrentBakery and your saved tracker credentials, so either keep it on a trusted network or turn the login on.

## Turn it on
*Settings → Login & 2FA*: choose a user name and a password (at least 12 characters). Then add a **TOTP second factor**: the page shows a setup key for any authenticator app (Aegis, 2FAS, Google Authenticator, a password manager …), you confirm with a code and receive **ten one-time recovery codes**, shown once. Store them.

## What is protected, and how
- Passwords are stored only as a salted **scrypt** hash (64 MiB) in `config.toml` (mode 0600), with the TOTP key and SHA-256 hashes of the unused recovery codes. Nothing secret is ever rendered again.
- Sessions are random tokens held in the server's memory (cookie `HttpOnly`, `SameSite=Strict`, `Secure` on https): at most 12 hours, 2 hours idle; ended by *Log out*, a password change or a service restart.
- A TOTP code works once (replay protection). Between password and code the browser holds only a 5-minute pending token that grants nothing; three wrong codes end it.
- Failed logins are throttled per address (the delay doubles after the third failure) and in total (20 per 15 minutes).
- Changing credentials or turning 2FA/the login off asks for the current password (and a fresh code).
- Cross-site POSTs are refused; responses carry `X-Frame-Options: DENY` and `Cache-Control: no-store`.

> [!IMPORTANT]
> Over plain http the login protects against guessing, not against someone reading your network traffic. Put TorrentBakery behind **https** (a [reverse proxy](installation.md#behind-a-reverse-proxy-https)) as soon as it leaves your trusted network.

## Locked out?
On the server:
```sh
tb auth status
tb auth set-password USER      # new password (asked for, never on the command line)
tb auth disable                # login and 2FA off
sudo systemctl restart torrentbakery   # ends all sessions, if the old password may be known
```

## Secrets in general
API keys, cookies and tokens are stored only in `config.toml` and never shown again after saving. Back that file up like a password store. Announce URLs contain your passkey: the pages only ever show their host.
