# Tracker templates

A tracker template is one small TOML file that describes a tracker site. TorrentBakery reads it to know **what login the site needs, which image hosts work, and how its upload form looks**. A *tracker profile* (Settings → Tracker profiles) is then just a template plus your own values (secrets, category, announce URL, …).

Templates are **data only**. They choose one of the built-in *engines* (`gazelle`, `unit3d`) and configure it; they never contain code, so a template from the community cannot run anything.

Built-in templates: `happyfappy` (Gazelle), `bitporn` (UNIT3D). Community templates: drop `*.toml` into `<config dir>/tracker-templates/` (the Settings page shows the exact path and lists files that could not be read, with the reason). A community file with the same `id` replaces the built-in one.

## 1. Authentication

What a site needs depends on the engine. A template lists the credentials the user has to supply, each with a label and a help text that the form shows:

| engine | main secret (`cookie`) | optional second secret (`session_cookie`) | announce URL |
|---|---|---|---|
| gazelle | browser session cookie (`name=value; …`), expires | – | read from the upload page (contains your passkey) |
| unit3d | API token (Bearer), does not expire | browser session cookie, for what the API cannot do (list cover/banner, own image upload) | not needed, the tracker puts your passkey in |

Both fields are stored in the config file (0600) and never shown again. No username/password login, no 2FA handling: a site that needs this is out of scope (use the cookie).

A credential is either a single value (an API token) or a list of **named cookies**. For cookies the template fixes the names and the user pastes only the values, one input per cookie, so nobody has to assemble `name=value; name2=value2` by hand:

```toml
[[auth.credentials]]
field    = "cookie"            # "cookie" (main secret, required) or "session_cookie"
label    = "API token"
required = true
help     = "Account settings -> Security -> API token."

[[auth.credentials]]
field    = "session_cookie"
label    = "Web session"
required = false
help     = "Log in with \"Remember me\" ticked, then copy the values of these cookies."
cookies  = [
  { name = "bitporn_session", label = "Session", level = "required" },
  { name = "remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d", label = "Remember me", level = "recommended" },   # level: required | recommended | optional
]
```

The profile stores the composed string; a blank input keeps the saved value, and cookies saved earlier that the template does not list are kept. (Laravel sites such as UNIT3D always name the remember cookie `remember_web_59ba36addc2b2f9401580f014c7f58ea4e30989d`.)

## 2. Image hosts

Which hosts a tracker accepts is a property of the *tracker*, not of TorrentBakery:

* **External hosts** (pixhost.to/.cc/pixho.st, imgbox, imagebam, hamsterimg, imghost.dev): the post just links the images. Works wherever the tracker allows hot-linked images.
* **Tracker-bound host**: an upload endpoint of the tracker itself (today: `bitporn.eu`, the site's own form, uses the web session cookie). Only offered with its tracker.
* Some trackers show foreign images through a proxy that blocks adult hosts (bitporn: wsrv.nl blocks pixhost). Such a template restricts the list.

```toml
[images]
hosts = ["bitporn.eu", "imghost.dev"]   # empty list = every host works
```

The start page only offers the hosts a template allows (and the tracker-bound ones only with their tracker). Adding a new *kind* of host (a new upload API) needs code in `hosts/`; a template can only choose among the existing ones.

## 3. Settings

Two levels:

**Template** (fixed per site): `id`, `name`, `engine`, `description`, default `base_url`, `source` flag, `private`, credentials, allowed image hosts, engine specifics (below).

**Profile** (per user, Settings → Tracker profiles): announce URL, source (overrides the template), private, site address (mirror/other domain), default category, anonymous upload, title template (Jinja), auto-publish after `tb bake`, plus the credentials. Everything blank falls back to the template.

Engine specifics:

```toml
[gazelle]
field_ids = { tags = "tags" }          # element ids of the upload form fields, only what differs from title/desc/image/taginput

[unit3d]
type_id = 1                            # ids are per installation, the API has no lookup endpoint
[unit3d.categories]
4 = "Amateur"
[unit3d.resolutions]
13 = "1080p(FHD)"
```

Categories of a Gazelle site are read from its upload form, so they are not in the template.

## Full example

See `src/torrentbakery/trackers/templates/happyfappy.toml` (small) and `bitporn.toml` (UNIT3D with ids).

## Writing a template for a new site

1. Pick the engine: `upload.php` form with a session cookie → `gazelle`; `/api/torrents/upload` with a token → `unit3d`. Other software needs a new engine (code).
2. Copy the closest built-in template, change `id` (lowercase), `name`, `base_url`, `source`.
3. UNIT3D: fill `type_id`, `categories` and `resolutions` from the site's upload form (select options).
4. Set `[images] hosts` if the site blocks some hosts.
5. Drop it in the templates folder, reload Settings, create a profile, press **Test login**.

Format version: `schema = 1`. Files with another schema are rejected with a message.
