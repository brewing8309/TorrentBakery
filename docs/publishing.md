# 📤 Publishing to trackers

[← Documentation](README.md)

A **tracker profile** that uses a **tracker template** can upload a baked release for you, then add it to qBittorrent and confirm it seeds. TorrentBakery does server-side what the [stash-empornium](https://github.com/bdbenim/stash-empornium) userscript does in the browser.

Built-in templates: **HappyFappy** (`happyfappy`, Gazelle) and **bitporn** (`bitporn`, UNIT3D). Others: [add your own tracker](tracker-templates.md).

## Set up a profile
**Web UI:** *Settings → Tracker profiles → Add*, choose the template, paste what it asks for (the form names each cookie and where to find it), press **Test login**. It lists the tracker's categories; pick a default.

**CLI:**
```sh
tb tracker add hf -s HF                          # announce URL optional for publishing profiles
tb tracker set hf --site happyfappy --cookie 'cid=…; sid=…' --category Amateur
tb tracker test hf                               # logs in, lists category ids; posts nothing
tb tracker fetch-announce hf                     # reads your announce URL from the upload page
```

## How a publish works
1. **Bake** with an image host (the tracker needs a cover URL).
2. **Review** on the result page: title, post, tags and category, prefilled and editable; categories are read live from the tracker. In the web UI nothing is ever uploaded without this step.
3. **Publish**: TorrentBakery posts the release to the tracker.
4. **Seed**: the torrent goes to qBittorrent, which rechecks your files. Only when they are complete is the torrent started; TorrentBakery waits until it really seeds.

A failed upload never breaks the bake; the reason is shown on the result page. On the command line:
```sh
tb publish out/My_Release -t hf --dry-run        # shows what would be sent
tb publish out/My_Release -t hf                  # uploads (public!) and adds to qBittorrent
tb bake stash:1234 -t hf -u pixhost.to --publish # bake and publish in one go
```
`--publish` (or the profile's *auto-publish*) uploads straight after the bake, on the command line only.

## Gazelle sites (e.g. HappyFappy)
- Login with your **browser session cookies**; the template names them, you paste the values.
- TorrentBakery reads `upload.php`, fills the form (title, post, tags, cover, category, anonymity, MediaInfo) and submits it with the torrent.
- Your announce URL (with passkey) can be read from the upload page: `tb tracker fetch-announce`.
- Cookies expire. **Test login** tells you; paste fresh ones.

## UNIT3D sites (e.g. bitporn)
- Login with the **API token** (account settings → Security). It does not expire. No announce URL needed: the tracker adds your passkey, and the torrent that is seeded is the one the tracker serves.
- Tags go into the keywords field, the BBCode post into the description.
- The API cannot set the list **cover and banner**. With the optional **web session** cookie, TorrentBakery cuts a 400×600 poster and a 960×540 banner from the release, uploads them to the bake's image host and enters them through the edit form. Keep the *remember me* cookie in the profile, then the session renews itself. **Set cover and banner again** on the result page retries.

## Seeding with qBittorrent
```sh
tb qbit set --url http://10.0.0.5:8080 --username u --password p --path-map /media/share=/data
tb qbit test
```
- **Path map**: where qBittorrent sees the files, if different from here (`<path here>=<path in qBittorrent>`).
- qBittorrent must see the original files (the torrent points at the video, not at a copy).
- If the files are incomplete at the save path (wrong mapping), the torrent is left stopped instead of downloading, and the result page says so. **Add to qBittorrent again** fixes it after you corrected the mapping; `tb seed` does the same on the command line.

> [!CAUTION]
> Publishing is public and usually cannot be undone on the tracker side. Use `--dry-run` or the review step with every new tracker or template, and only upload what you have the right to share and what the tracker's rules allow.
