from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import typer

from . import __version__
from . import config as config_mod
from .config import SITE_SOURCES, StashBoxCfg, Tracker
from .hosts import HOST_NAMES
from .service import bake_source

app = typer.Typer(help="TorrentBakery – torrent, screenshot sheet and forum post from a video file or Stash scene.", no_args_is_help=True)
tracker_app = typer.Typer(help="Manage saved tracker profiles.", no_args_is_help=True)
stash_app = typer.Typer(help="Stash and stash-box connections.", no_args_is_help=True)
app.add_typer(tracker_app, name="tracker")
app.add_typer(stash_app, name="stash")


@app.command("version")
def version_cmd() -> None:
    """Print the version."""
    typer.echo(__version__)


@app.callback()
def main() -> None:
    """TorrentBakery."""


@app.command("bake")
def bake_cmd(
    source: str = typer.Argument(..., help="Video file, or stash:<scene-id>"),
    out: Path = typer.Option(Path("out"), "--out", "-o", envvar="TB_OUT", help="Output root directory"),
    tracker: str = typer.Option(None, "--tracker", "-t", help="Tracker profile (see `tb tracker list`)"),
    cover: Path = typer.Option(None, "--cover", exists=True, dir_okay=False, help="Use this image/GIF/WebP as the cover instead of an animation"),
    cover_source: str = typer.Option(None, "--cover-source", help="own = build the animation, stash = use Stash's own preview if the scene has one"),
    cover_format: str = typer.Option(None, "--cover-format", help="webp or gif (webp falls back to gif for hosts that need it)"),
    upload: str = typer.Option(None, "--upload", "-u", help=f"Upload images to a host ({', '.join(HOST_NAMES)})"),
    sfw: bool = typer.Option(False, "--sfw", help="Mark uploads as family safe (default: adult content)"),
    stashbox: str = typer.Option(None, "--stashbox", help="Scene URL on a stash-box, e.g. https://stashdb.org/scenes/<id> (for plain files)"),
    publish: bool = typer.Option(None, "--publish/--no-publish", help="Upload to the tracker after baking (default: the profile's auto_publish; needs --upload for the cover URL)"),
) -> None:
    cfg = config_mod.load()
    try:
        result = bake_source(cfg, source, out, tracker=tracker, cover=cover, upload=upload, nsfw=not sfw, stashbox=stashbox, cover_source=cover_source, cover_format=cover_format, publish=publish)
    except (ValueError, KeyError) as e:
        raise typer.BadParameter(str(e.args[0] if e.args else e))
    typer.echo(f"Baked into {result} (+ {result}.zip)")
    if (result / "publish-error.txt").exists():
        typer.echo(f"Publishing failed: {(result / 'publish-error.txt').read_text().strip()}", err=True)
        raise typer.Exit(1)
    t = cfg.trackers.get(tracker) if tracker else None
    if t and t.site and (t.auto_publish if publish is None else publish):  # publish.json may be left over from an earlier run otherwise
        typer.echo(f"Published: {json.loads((result / 'publish.json').read_text())[tracker]['torrent_url']}")


@tracker_app.command("add")
def tracker_add(
    name: str,
    announce: list[str] = typer.Option(None, "--announce", "-a", help="Announce URL (repeatable); optional for publishing profiles, `tb tracker fetch-announce` reads it from the site"),
    source: str = typer.Option("", "--source", "-s", help="Source tag written into the torrent"),
    private: bool = typer.Option(True, "--private/--public"),
) -> None:
    """Add or replace a tracker profile."""
    cfg = config_mod.load()
    cfg.trackers[name] = replace(cfg.trackers.get(name, Tracker()), announce=announce or [], source=source, private=private)
    config_mod.save(cfg)
    typer.echo(f"Saved tracker {name}")


@tracker_app.command("list")
def tracker_list() -> None:
    cfg = config_mod.load()
    for name, t in cfg.trackers.items():
        pub = f"  publish={t.site}{' (auto)' if t.auto_publish else ''}" if t.site else ""
        typer.echo(f"{name}: {', '.join(t.announce)}  source={t.effective_source or '-'}  {'private' if t.private else 'public'}{pub}")
    if not cfg.trackers:
        typer.echo("No tracker profiles yet; add one with `tb tracker add`.")


@tracker_app.command("remove")
def tracker_remove(name: str) -> None:
    cfg = config_mod.load()
    if cfg.trackers.pop(name, None) is None:
        raise typer.BadParameter(f"unknown tracker {name!r}")
    config_mod.save(cfg)
    typer.echo(f"Removed tracker {name}")


@stash_app.command("set")
def stash_set(url: str, api_key: str = typer.Option("", "--api-key")) -> None:
    """Set the Stash base URL (e.g. http://stash:9999) and API key."""
    cfg = config_mod.load()
    cfg.stash.url, cfg.stash.api_key = url.rstrip("/"), api_key
    config_mod.save(cfg)
    typer.echo("Stash saved")


@stash_app.command("box-add")
def stash_box_add(name: str, endpoint: str, api_key: str = typer.Option("", "--api-key")) -> None:
    """Save a stash-box endpoint (…/graphql) and key for use with --stashbox."""
    cfg = config_mod.load()
    cfg.stashboxes[name] = StashBoxCfg(endpoint, api_key)
    config_mod.save(cfg)
    typer.echo(f"Saved stash-box {name}")


@stash_app.command("map-add")
def stash_map_add(stash_path: str, local_path: str) -> None:
    """Map a path prefix as Stash sees it to where the share is mounted here."""
    cfg = config_mod.load()
    cfg.path_map.append((stash_path, local_path))
    config_mod.save(cfg)
    typer.echo(f"Mapped {stash_path} -> {local_path}")


def _tracker(cfg: config_mod.Config, name: str) -> Tracker:
    if name not in cfg.trackers:
        raise typer.BadParameter(f"unknown tracker {name!r}; known: {', '.join(cfg.trackers) or 'none'}")
    return cfg.trackers[name]


def _category_id(cfg: config_mod.Config, name: str, t: Tracker, category: str | None, site: str | None, url: str | None, cookie: str | None) -> str | None:
    """A category given by name becomes its id, using the tracker's own list (with the options of this same command applied)."""
    if not category or category.isdigit():
        return category
    from dataclasses import replace

    from .trackers import TrackerError, get_publisher
    probe = replace(t, site=site or t.site, base_url=(url or t.base_url).rstrip("/"), cookie=cookie or t.cookie)
    try:
        cats = get_publisher("_probe", replace(cfg, trackers={**cfg.trackers, "_probe": probe})).fetch_form().categories
    except (TrackerError, ValueError, KeyError) as e:
        raise typer.BadParameter(f"cannot look up the category name ({e}); give its id instead") from e
    hit = [v for v, label in cats if label.lower() == category.lower()]
    if not hit:
        raise typer.BadParameter(f"{category!r} is not offered by {name!r}; known: {', '.join(label for _, label in cats)}")
    return hit[0]


@tracker_app.command("set")
def tracker_set(
    name: str,
    site: str = typer.Option(None, "--site", help=f"Site type: {', '.join(SITE_SOURCES)}"),
    url: str = typer.Option(None, "--url", help="Site address, e.g. https://www.empornium.sx"),
    cookie: str = typer.Option(None, "--cookie", help="Session cookie, e.g. 'session=abc; other=def' (copy from your logged-in browser); API token for UNIT3D sites"),
    category: str = typer.Option(None, "--category", help="Default upload category: its name (e.g. Amateur) or id (see `tb tracker test`)"),
    anonymous: bool = typer.Option(None, "--anonymous/--no-anonymous"),
    title_template: str = typer.Option(None, "--title-template", help="Jinja template for the torrent title"),
    auto_publish: bool = typer.Option(None, "--auto-publish/--no-auto-publish", help="Publish right after baking with this tracker"),
) -> None:
    """Set the publishing options of a tracker profile (create it first with `tb tracker add`)."""
    cfg = config_mod.load()
    t = _tracker(cfg, name)
    if site is not None and site not in SITE_SOURCES:
        raise typer.BadParameter(f"site must be one of: {', '.join(SITE_SOURCES)}")
    category = _category_id(cfg, name, t, category, site, url, cookie)
    for k, v in (("site", site), ("base_url", url.rstrip("/") if url else url), ("cookie", cookie), ("category", category), ("anonymous", anonymous),
                 ("title_template", title_template), ("auto_publish", auto_publish)):
        if v is not None:
            setattr(t, k, v)
    config_mod.save(cfg)
    typer.echo(f"Saved publishing options of {name}")


@tracker_app.command("test")
def tracker_test(name: str) -> None:
    """Log in with the saved cookie, list the upload categories (nothing is posted)."""
    from .trackers import TrackerError, get_publisher

    try:
        form = get_publisher(name, config_mod.load()).fetch_form()
    except (TrackerError, ValueError, KeyError) as e:
        raise typer.Exit(typer.echo(f"FAILED: {e}", err=True) or 1)
    typer.echo(f"{name}: logged in, upload form found" + (", announce URL available (`tb tracker fetch-announce`)" if form.announce else ""))
    for value, label in form.categories:
        typer.echo(f"  category {value}: {label}")


@tracker_app.command("fetch-announce")
def tracker_fetch_announce(name: str) -> None:
    """Read your personal announce URL from the tracker's upload page and save it in the profile."""
    from .trackers import TrackerError, get_publisher

    cfg = config_mod.load()
    t = _tracker(cfg, name)
    try:
        form = get_publisher(name, cfg).fetch_form()
    except (TrackerError, ValueError, KeyError) as e:
        raise typer.Exit(typer.echo(f"FAILED: {e}", err=True) or 1)
    if not form.announce:
        raise typer.Exit(typer.echo("The site shows no announce URL (UNIT3D sites need none: the tracker puts your passkey in itself).", err=True) or 1)
    t.announce = [form.announce]
    config_mod.save(cfg)
    typer.echo(f"Saved announce URL for {name} (host {form.announce.split('/')[2]})")


@app.command("publish")
def publish_cmd(
    folder: Path = typer.Argument(..., help="A bake folder (contains release.json)"),
    tracker: str = typer.Option(..., "--tracker", "-t"),
    title: str = typer.Option(None, "--title"),
    category: str = typer.Option(None, "--category"),
    dry_run: bool = typer.Option(False, "--dry-run", help="Fetch the upload form and show what would be sent; post nothing"),
) -> None:
    """Upload a baked release to a tracker and add it to qBittorrent."""
    from .publish import publish
    from .trackers import TrackerError

    cfg = config_mod.load()
    try:
        out = publish(cfg, folder, tracker, title=title, category=category, dry_run=dry_run, progress=lambda s, f=0.0: typer.echo(f"  {s}"))
    except (TrackerError, ValueError, KeyError) as e:
        raise typer.Exit(typer.echo(f"FAILED: {e}", err=True) or 1)
    if dry_run:
        typer.echo("Dry run, nothing was posted. Form would contain:")
        for k, v in out["sent"].items():
            typer.echo(f"  {k}: {v if len(v) < 200 else v[:200] + '…'}")
        return
    typer.echo(f"Published: {out['torrent_url']}")
    if out["qbit"]:
        typer.echo(f"qBittorrent: {out['qbit']['message']}")
        if not out["qbit"]["ok"]:
            typer.echo("(retry with `tb seed` once it is fixed)")
    if out["suggestions"]:
        typer.echo("Unknown tags (sent as-is): " + ", ".join(f"{k} -> {v}" for k, v in out["suggestions"].items()) + "  (teach them with `tb tags map`)")


@app.command("seed")
def seed_cmd(folder: Path = typer.Argument(..., help="A published bake folder"), tracker: str = typer.Option(..., "--tracker", "-t")) -> None:
    """Add an already published release to qBittorrent (again), using the torrent file the tracker serves."""
    from .clients import QbitError
    from .publish import seed
    from .trackers import TrackerError

    try:
        r = seed(config_mod.load(), folder, tracker, progress=lambda s, f=0.0: typer.echo(f"  {s}"))
    except (TrackerError, QbitError, ValueError, KeyError) as e:
        raise typer.Exit(typer.echo(f"FAILED: {e}", err=True) or 1)
    typer.echo(r["message"])
    if not r["ok"]:
        raise typer.Exit(1)


qbit_app = typer.Typer(help="qBittorrent connection for seeding published torrents.", no_args_is_help=True)
tags_app = typer.Typer(help="Stash tag -> tracker tag mapping.", no_args_is_help=True)
app.add_typer(qbit_app, name="qbit")
app.add_typer(tags_app, name="tags")


@qbit_app.command("set")
def qbit_set(
    url: str = typer.Option(None, "--url", help="WebUI address, e.g. http://10.10.1.5:8080"),
    username: str = typer.Option(None, "--username"),
    password: str = typer.Option(None, "--password"),
    category: str = typer.Option(None, "--category"),
    start: bool = typer.Option(None, "--start/--no-start", help="Start seeding after the recheck"),
    path_map: list[str] = typer.Option(None, "--path-map", help="FROM=TO: a folder here and the same folder as qBittorrent sees it (repeatable, replaces the saved list)"),
) -> None:
    cfg = config_mod.load()
    q = cfg.qbit
    for k, v in (("url", url.rstrip("/") if url else url), ("username", username), ("password", password), ("category", category), ("start", start)):
        if v is not None:
            setattr(q, k, v)
    if path_map:
        q.path_map = [tuple(m.split("=", 1)) for m in path_map]  # type: ignore[misc]
    config_mod.save(cfg)
    typer.echo("Saved qBittorrent settings")


@qbit_app.command("test")
def qbit_test() -> None:
    from .clients import QbitError, QBittorrent

    try:
        typer.echo(QBittorrent(config_mod.load().qbit).test())
    except QbitError as e:
        raise typer.Exit(typer.echo(f"FAILED: {e}", err=True) or 1)


@tags_app.command("map")
def tags_map(stash_tag: str, tracker_tags: str, tracker: str = typer.Option("", "--tracker", "-t", help="Only for this tracker profile")) -> None:
    """Map a Stash tag to space-separated tracker tags, e.g. `tb tags map "Big Tits" "big.tits boobs"`."""
    from .tags import load_user_map, save_user_map

    data = load_user_map()
    (data.setdefault("tracker", {}).setdefault(tracker, {}) if tracker else data.setdefault("tags", {}))[stash_tag] = tracker_tags
    save_user_map(data)
    typer.echo(f"{stash_tag} -> {tracker_tags}" + (f" (for {tracker})" if tracker else ""))


@tags_app.command("ignore")
def tags_ignore(stash_tag: str) -> None:
    """Never send this Stash tag."""
    from .tags import load_user_map, save_user_map

    data = load_user_map()
    data["ignored"] = sorted({*data.get("ignored", []), stash_tag})
    save_user_map(data)
    typer.echo(f"Ignoring {stash_tag}")


@tags_app.command("show")
def tags_show(stash_tag: str, tracker: str = typer.Option("", "--tracker", "-t")) -> None:
    from .tags import TagMap

    mapped, known = TagMap.load().map_tag(stash_tag, tracker)
    typer.echo((" ".join(mapped) or "(ignored)") + ("" if known else "  [unknown: suggestion]"))


host_app = typer.Typer(help="Image host settings.", no_args_is_help=True)
app.add_typer(host_app, name="host")


@host_app.command("list")
def host_list() -> None:
    cfg = config_mod.load()
    for name in HOST_NAMES:
        opts = cfg.hosts.get(name, {})
        typer.echo(f"{name}" + (f"  ({', '.join(opts)})" if opts else ""))


@host_app.command("set")
def host_set(name: str, option: list[str] = typer.Option(..., "--option", "-o", help="key=value, e.g. api_key=…")) -> None:
    """Save host options (hamsterimg and imghost.dev need api_key; optional base_url)."""
    if name not in HOST_NAMES:
        raise typer.BadParameter(f"unknown host {name!r}; known: {', '.join(HOST_NAMES)}")
    cfg = config_mod.load()
    for o in option:
        k, _, v = o.partition("=")
        cfg.hosts.setdefault(name, {})[k] = v
    config_mod.save(cfg)
    typer.echo(f"Saved options for {name}")


@app.command("serve")
def serve(
    host: str = typer.Option("127.0.0.1", "--host", envvar="TB_HOST", help="Bind address (the UI has no login: keep it on a trusted network)"),
    port: int = typer.Option(8080, "--port", envvar="TB_PORT"),
    out: Path = typer.Option(Path("out"), "--out", "-o", envvar="TB_OUT", help="Output root directory"),
) -> None:
    """Start the web UI."""
    import uvicorn

    from .web.app import create_app

    uvicorn.run(create_app(out_root=out), host=host, port=port)
