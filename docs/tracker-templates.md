# 🧩 Add your own tracker

[← Documentation](README.md)

A tracker is described by a **tracker template**: one small TOML file that tells TorrentBakery *what login the site needs, which image hosts work and how its upload form looks*. Your own values (cookie, announce URL, category …) live in a **tracker profile** that uses the template.

Templates are **data only**. They pick one of the built-in *engines* and configure it; they never contain code, so a template from someone else cannot run anything on your server.

| Engine | Tracker software | Login | Examples |
|---|---|---|---|
| `gazelle` | Gazelle / Ocelot sites with `upload.php` | browser session cookies | `happyfappy` (built in), Empornium-style sites |
| `unit3d` | UNIT3D with `/api/torrents/upload` | API token (+ optional web session) | `bitporn` (built in) |

Software other than these two needs a new engine, which is code: open an issue.

> [!TIP]
> Only need a torrent with your announce URL and no automatic upload? You need no template at all: *Settings → Tracker profiles → Add*, enter announce URL and source flag, done.

## Step by step

### 1. Find out the engine
Open the site's upload page. Is it `…/upload.php` with a classic form → **gazelle**. Is the site UNIT3D (footer says so; API token under account settings → Security) → **unit3d**.

### 2. Copy the closest built-in template
The built-in ones are in [`src/torrentbakery/trackers/templates/`](../src/torrentbakery/trackers/templates/): `happyfappy.toml` (Gazelle, small) and `bitporn.toml` (UNIT3D, with ids). Save your copy as `<id>.toml`.

### 3. Fill in the basics
```toml
schema = 1
id = "mytracker"                     # lowercase, unique; a file with the id of a built-in replaces it
name = "My Tracker"
engine = "gazelle"                   # gazelle | unit3d
description = "Gazelle tracker. Fills in and posts upload.php with your browser session."
base_url = "https://mytracker.example"
source = "MT"                        # source flag written into the torrent (empty for UNIT3D)
private = true
```

### 4. Say which login it needs
Name the cookies; users then paste only the values, one field each. Find them in the browser's developer tools (Application/Storage → Cookies) while logged in.
```toml
[[auth.credentials]]
field = "cookie"
label = "Login cookies"
required = true
help = "Log in at mytracker.example, then copy the values of these cookies."
cookies = [
  { name = "session", label = "Session" },
]
```

### 5. Restrict image hosts (only if needed)
If the site shows foreign images through a proxy that blocks some hosts, list the ones that work:
```toml
[images]
hosts = []        # empty = every host works
```

### 6. Engine details
**Gazelle:** the form fields are found by element id. The defaults are `title`, `desc`, `image` and `taginput` (as on stash-empornium-style forms). Only list what differs (look at the form with the browser's inspector):
```toml
[gazelle]
field_ids = { tags = "tags" }
```
Categories are read live from the form, so they are not in the template.

**UNIT3D:** the API has no lookup endpoint, so the ids come from the site's upload form (the `<select>` options):
```toml
[unit3d]
type_id = 1
[unit3d.categories]
4 = "Amateur"
[unit3d.resolutions]
13 = "1080p(FHD)"
```

### 7. Install it
Copy the file into the templates folder of your installation:

| Installation | Folder |
|---|---|
| installed service | `/var/lib/torrentbakery/tracker-templates/` |
| from a checkout | `~/.config/torrentbakery/tracker-templates/` (next to your `config.toml`) |

*Settings → Tracker profiles* shows the exact path and lists files that could not be read, with the reason. No restart needed; reload the page.

### 8. Test it
Create a profile with your template, paste the cookies/token, press **Test login** (`tb tracker test NAME`): it logs in and lists the categories, posting nothing. Then bake something and look at the review step, or run `tb publish out/<release> -t NAME --dry-run`.

### 9. Share it
Works? Please contribute it, so others don't have to start over: open a pull request adding the file to `src/torrentbakery/trackers/templates/`, or attach it to an issue. Never include your cookies or tokens; they belong in the profile, not the template.

---

# Reference

## Authentication

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

## Image hosts

Which hosts a tracker accepts is a property of the *tracker*, not of TorrentBakery:

* **External hosts** (pixhost.to/.cc/pixho.st, imgbox, imagebam, hamsterimg, imghost.dev): the post just links the images. Works wherever the tracker allows hot-linked images.
* **Tracker-bound host**: an upload endpoint of the tracker itself (today: `bitporn.eu`, the site's own form, uses the web session cookie). Only offered with its tracker.
* Some trackers show foreign images through a proxy that blocks adult hosts (bitporn: wsrv.nl blocks pixhost). Such a template restricts the list.

```toml
[images]
hosts = ["bitporn.eu", "imghost.dev"]   # empty list = every host works
```

The start page only offers the hosts a template allows (and the tracker-bound ones only with their tracker). Adding a new *kind* of host (a new upload API) needs code in `hosts/`; a template can only choose among the existing ones.

## Settings

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

## Format version
`schema = 1`. Files with another schema are rejected with a message.
