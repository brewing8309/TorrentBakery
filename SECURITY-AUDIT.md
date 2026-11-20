# Security Audit Review

| | |
|---|---|
| **Project** | TorrentBakery |
| **Version reviewed** | 0.1.0 plus the hardening changes listed under *Unreleased* in the [changelog](CHANGELOG.md) |
| **Date** | 2026-11-20 |
| **Type** | White-box review: source code, configuration and packaging, with dynamic testing |
| **Performed by** | AI-assisted review (Claude Code) on behalf of the maintainer |

## 1. Summary
TorrentBakery is a self-hosted tool for a single operator. It handles valuable secrets: tracker passkeys, session cookies, API tokens, the qBittorrent login and the Stash API key. It runs external media tools on files from the operator's library. Its web interface has to work on a home network both with and without a login.

The application uses a small, deliberate set of security controls. All of them are implemented with the Python standard library or the mature frameworks the project already depends on. The review found the design sound. Points identified during the review were fixed and covered by regression tests before this report was finalized. The test suite (146 tests) passes on the reviewed version.

**Overall assessment: good**, for its intended deployment as a single-user service on a trusted network or behind an HTTPS reverse proxy with the login enabled. The remaining risks are listed in section 6. They come from the deployment model, not from defects in the code.

## 2. Scope
**In scope**
- The web application (`src/torrentbakery/web`): routing, authentication, two-factor login, sessions, request guard, templates and client-side scripts.
- The bake pipeline and its sources: file selection, output handling, Stash and stash-box access, external process execution (ffmpeg, ffprobe, mediainfo).
- Outbound integrations: image hosts, Gazelle and UNIT3D trackers, qBittorrent.
- Configuration storage and secret handling (`config.toml`, `tags.toml`).
- Packaging and operations: `install.sh`, `uninstall.sh`, the systemd unit, the CLI wrapper, the release workflow.
- Third-party dependencies.

**Out of scope**
- The security of the third-party services themselves: trackers, image hosts, Stash, qBittorrent.
- The host operating system, the hypervisor and the network around the deployment.
- The internals of ffmpeg and mediainfo (treated as an attack surface, see 6).

## 3. Methodology
1. **Architecture and threat modelling.** Identified the assets, trust boundaries and realistic attackers (section 4).
2. **Manual code review** of every request handler, of authentication and session code, of all file system and subprocess calls, and of all outbound HTTP clients. The review focused on how untrusted input flows to sensitive sinks.
3. **Dynamic testing** against a local instance with synthetic credentials:
   - cross-origin and DNS-rebinding requests;
   - credential and redirect handling;
   - brute-force and lock-out behaviour;
   - path and file-name edge cases;
   - authentication bypass attempts through path normalisation;
   - open-redirect probes;
   - cookie attribute checks.
4. **Browser verification** in headless Chromium. All pages load under the Content-Security-Policy without violations.
5. **Dependency audit** with `pip-audit` against the resolved environment. No known vulnerabilities were reported.
6. **Service isolation** measured with `systemd-analyze security`. The unit now scores **1.4 (OK)**.
7. **Regression tests** for every hardening change (`tests/test_security.py`, `tests/test_auth.py`).

## 4. Threat model
| Asset | Why it matters |
|---|---|
| Tracker announce URLs, cookies, API tokens | Allow uploads and downloads on the operator's account; leaking them can get the account banned |
| qBittorrent and Stash credentials | Control over the download client and the media library |
| Login password, TOTP secret, recovery codes | Access to everything above |
| Media library | Private content; must only be read, never changed or published by accident |

| Attacker | Capability assumed |
|---|---|
| Malicious web page opened by the operator | Can make the operator's browser send requests (CSRF) and resolve its own domain to private addresses (DNS rebinding) |
| Other device or user on the LAN | Can reach the port directly |
| Compromised or malicious upstream service | Controls responses and redirects from a tracker, image host or stash-box |
| Crafted media or metadata | File names, scene metadata and video containers coming from outside |

## 5. Control assessment

| Area | Assessment | Observations |
|---|---|---|
| **Authentication** | Strong | Optional login with salted scrypt hashes (64 MiB), constant-time comparison and equal work for unknown user names. RFC 6238 TOTP with replay protection, serialised so a code cannot be used twice in parallel. Ten 80-bit recovery codes, stored only as hashes. Changing a credential requires re-authentication. |
| **Sessions** | Strong | 256-bit random tokens, stored hashed and only in memory. `HttpOnly`, `SameSite=Strict`, `Secure` on https. Absolute and idle lifetimes. A fresh token on every login, so no session fixation. A credential change ends all other sessions. |
| **Brute-force protection** | Good | Exponential back-off per client (IPv6 per /64) and a global failure window. Clients that have not failed are never locked out. Behind a proxy, trusted proxy addresses are configurable, so the real client is counted. |
| **Cross-site request forgery** | Strong | Every state-changing request is checked against `Origin` and `Sec-Fetch-Site`, whether or not the login is enabled. With the login on, `SameSite=Strict` adds a second layer. |
| **DNS rebinding / Host validation** | Good | Without a login, the UI only answers to IP literals, `localhost`, single-label names and an explicit allow-list (`TB_ALLOWED_HOSTS`). With the login on, the origin-bound session cookie provides the protection. |
| **Output encoding / XSS** | Strong | Jinja2 auto-escaping in all pages. The client side builds DOM nodes with `textContent`. The BBCode preview escapes first and only turns known tags and `http(s)` URLs into markup. A Content-Security-Policy blocks foreign scripts, framing and foreign form targets. |
| **Open redirects** | Strong | Post-login targets are limited to local paths, with protocol-relative, backslash and control-character variants rejected. |
| **Secret handling** | Strong | Secrets are stored only in `config.toml` (mode 0600, atomic write, service `UMask=0077`) and never rendered back. Forms report only whether a secret exists. Every secret is bound to the address it was saved for. Pointing a profile at a new address drops the old secret, and test functions use saved secrets only for the saved address. Announce URLs are shown by host only. Passwords are never accepted on the command line. |
| **Outbound requests** | Good | TLS verification is on everywhere. Secret-bearing headers (API keys, cookies) are removed whenever a request or redirect leaves the site they belong to. `Authorization` headers rely on the HTTP client's own cross-origin stripping. Destinations are only those the operator configures. |
| **File system access** | Strong | The web UI reads media only inside the configured media folders, symlinks out of them are not followed, and the same rule applies to scenes resolved through Stash. Downloads are limited to names that exist in a job's own folder. Output folder names are sanitised and verified to stay inside the output root. Deletion only ever removes direct children of the output root. |
| **Process execution** | Strong | External tools are called with argument lists, never through a shell. Inputs are resolved absolute paths, so neither option injection nor ffmpeg protocol handlers apply. MediaInfo output is stripped of host paths before it is published. |
| **Templates** | Good | User-editable title templates run in Jinja2's `SandboxedEnvironment`. Post templates are loaded only from the package or from a directory the operator controls on the server. |
| **Transport security** | Adequate | Plain HTTP by design for LAN use. HTTPS is delegated to a reverse proxy, which is documented. Cookies switch to `Secure` automatically behind https. |
| **Service isolation** | Strong | Dedicated system user, read-only file system apart from the data directory, no capabilities, private `/tmp` and devices, kernel and cgroup protection, restricted address families and namespaces, a `@system-service` system-call filter (`systemd-analyze` exposure 1.4). |
| **Information exposure** | Good | No API browser or OpenAPI schema is exposed, there is no server banner, and pages are `no-store` when the login is on. Error messages are informative without leaking secrets or stack traces. |
| **Supply chain** | Adequate | Dependencies are few and widely used, with no known vulnerabilities at the time of review. Releases are built by CI from tags, after the test suite and a version check, and ship with a `SHA256SUMS` file. See 6 for further steps. |

## 6. Residual risks and recommendations
These are inherent to the deployment model or are optional further hardening. None of them is an open defect.

1. **The login is optional and off by default.** Anyone who can reach the port can use the stored credentials. The installer and documentation say so clearly. *Recommendation:* enable the login on every deployment that other people's devices can reach.
2. **Plain HTTP on the LAN.** Credentials and session cookies are readable by anyone who can sniff the network segment. *Recommendation:* use an HTTPS reverse proxy as soon as the service leaves a trusted network.
3. **Media parser attack surface.** ffmpeg and mediainfo parse files that may come from outside. The systemd sandbox contains the impact. *Recommendation:* keep the OS packages updated (unattended upgrades).
4. **CSP allows inline scripts and styles.** Output encoding is the primary XSS defence. *Recommendation:* moving the inline scripts into static files would allow a strict `script-src 'self'`.
5. **Secrets at rest are plain text** in a 0600 file inside a 0700 directory. This is appropriate for a single-host tool. *Recommendation:* include `config.toml` in encrypted backups only.
6. **Dependency pinning.** Versions are lower bounds, and GitHub Actions are referenced by tag. *Recommendation:* ship a hash-pinned lock file for the installer, pin Actions by commit SHA, and run `pip-audit` in CI.
7. **No persistent audit log.** Failed logins, recovery-code use and credential changes are logged to the journal only. *Recommendation:* forward the journal if the deployment needs an audit trail.

## 7. Conclusion
The reviewed version of TorrentBakery protects its secrets and its host well, and its security controls are consistent and tested. With the login enabled and HTTPS in front of it, it is suitable for exposure beyond the home network. Without the login it should stay on a trusted network, as its documentation advises. A new review is recommended after major changes to authentication, the request guard, or the outbound integrations.
